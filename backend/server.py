"""
Kimi Chat Server — FastAPI backend (v2.1)
"""
import os
import sys
import json
import uuid
import time
import base64
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

from fastapi import FastAPI, UploadFile, File, HTTPException, Header, Request
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.kimi_client import KimiClient, KimiAPIError, AVAILABLE_MODELS
from backend.tools import TOOL_DEFINITIONS
from backend.tools.search import web_search
from backend.tools.sandbox import run_code
from backend.tools.vision import analyze_image, store_image, get_image
from backend.tools.image_gen import generate_image
from backend import auth

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("kimi-chat")

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT / "frontend"
DATA_DIR = ROOT / "data"
CHATS_DIR = DATA_DIR / "chats"
IMAGES_DIR = DATA_DIR / "images"
CHATS_DIR.mkdir(parents=True, exist_ok=True)
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Kimi Chat", version="2.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ---------- Models ----------
class ChatMessage(BaseModel):
    role: str
    content: Any = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    name: Optional[str] = None


class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    chat_id: Optional[str] = None
    enable_tools: bool = True
    model: Optional[str] = None
    mode: str = "fast"  # lite | fast | thinking
    attached_images: Optional[List[str]] = None


class SaveChatRequest(BaseModel):
    chat_id: str
    title: str
    messages: List[Dict[str, Any]]


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    password: str


class ImageGenRequest(BaseModel):
    prompt: str
    width: int = 1024
    height: int = 1024


# ---------- Auth helpers ----------
def get_current_user(authorization: Optional[str]) -> Optional[Dict[str, Any]]:
    if not authorization:
        return None
    token = authorization[7:] if authorization.startswith("Bearer ") else authorization
    return auth.verify_token(token)


def require_user(authorization: Optional[str]) -> Dict[str, Any]:
    user = get_current_user(authorization)
    if not user:
        raise HTTPException(401, "Unauthorized")
    return user


# ---------- Health & config ----------
@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "model": os.getenv("KIMI_MODEL", "moonshotai/Kimi-K2.6"),
        "base_url": os.getenv("KIMI_BASE_URL", ""),
        "has_key": bool(os.getenv("KIMI_API_KEY")),
        "version": "2.1.0",
    }


@app.get("/api/models")
async def get_models():
    return {"models": AVAILABLE_MODELS}


@app.get("/api/debug/ping")
async def debug_ping():
    try:
        client = KimiClient()
    except Exception as e:
        return {"ok": False, "stage": "init", "error": f"{type(e).__name__}: {e}"}
    return await client.ping()


# ---------- Auth endpoints ----------
@app.post("/api/auth/register")
async def api_register(req: RegisterRequest):
    result = auth.register(req.username, req.password)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "Registration failed"))
    return auth.login(req.username, req.password)


@app.post("/api/auth/login")
async def api_login(req: LoginRequest):
    result = auth.login(req.username, req.password)
    if not result.get("ok"):
        raise HTTPException(401, result.get("error", "Login failed"))
    return result


@app.post("/api/auth/logout")
async def api_logout(authorization: Optional[str] = Header(None)):
    if authorization:
        token = authorization[7:] if authorization.startswith("Bearer ") else authorization
        auth.logout(token)
    return {"ok": True}


@app.get("/api/auth/me")
async def api_me(authorization: Optional[str] = Header(None)):
    user = get_current_user(authorization)
    if not user:
        raise HTTPException(401, "Not logged in")
    return {"username": user["username"], "is_owner": user["is_owner"]}


@app.get("/api/auth/users")
async def api_list_users(authorization: Optional[str] = Header(None)):
    user = require_user(authorization)
    if not user["is_owner"]:
        raise HTTPException(403, "Owner only")
    return {"users": auth.list_users()}


@app.delete("/api/auth/users/{username}")
async def api_delete_user(username: str, authorization: Optional[str] = Header(None)):
    user = require_user(authorization)
    if not user["is_owner"]:
        raise HTTPException(403, "Owner only")
    result = auth.delete_user(username)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "Delete failed"))
    return result


# ---------- Tool executor ----------
async def execute_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    log.info(f"[TOOL] {name} args_keys={list(args.keys())}")
    try:
        if name == "web_search":
            return await web_search(args.get("query", ""), int(args.get("max_results", 5)))
        if name == "run_code":
            return await run_code(args.get("language", "python"), args.get("code", ""))
        if name == "analyze_image":
            return await analyze_image(args.get("image_id", ""), args.get("question", ""))
        if name == "generate_image":
            return await generate_image(
                args.get("prompt", ""),
                int(args.get("width") or 1024),
                int(args.get("height") or 1024),
            )
        return {"error": f"Unknown tool: {name}"}
    except Exception as e:
        log.exception("Tool %s crashed", name)
        return {"error": f"Tool {name} failed: {type(e).__name__}: {e}"}


# ---------- Chat streaming endpoint ----------
def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


MODE_CONFIG = {
    "lite":     {"temperature": 0.4, "max_tokens": 1024,  "system_extra": "\n\nBe concise. Prefer short direct answers unless the user explicitly asks for detail."},
    "fast":     {"temperature": 0.7, "max_tokens": 4096,  "system_extra": ""},
    "thinking": {"temperature": 0.6, "max_tokens": 8192,
                 "system_extra": "\n\nThink step by step. Reason carefully about the problem, break it into parts, then give a clear final answer."},
}


def _tool_signature(name: str, args: Dict[str, Any]) -> str:
    """Compact signature to detect the model re-issuing the same tool call in a loop."""
    try:
        # Only key args matter (query for search, code for run_code, etc.)
        key_fields = {}
        if name == "web_search":
            key_fields = {"query": (args.get("query") or "").strip().lower()}
        elif name == "run_code":
            code = (args.get("code") or "").strip()
            key_fields = {"language": args.get("language"), "code_hash": hash(code)}
        elif name == "analyze_image":
            key_fields = {"image_id": args.get("image_id"), "question": (args.get("question") or "").strip().lower()}
        elif name == "generate_image":
            key_fields = {"prompt": (args.get("prompt") or "").strip().lower()}
        return f"{name}::{json.dumps(key_fields, sort_keys=True, ensure_ascii=False)}"
    except Exception:
        return f"{name}::?"


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest, authorization: Optional[str] = Header(None)):
    user = require_user(authorization)
    mode_cfg = MODE_CONFIG.get(req.mode, MODE_CONFIG["fast"])

    try:
        client = KimiClient(model=req.model) if req.model else KimiClient()
    except Exception as e:
        raise HTTPException(500, str(e))

    # Normalize messages
    messages: List[Dict[str, Any]] = []
    for m in req.messages:
        msg: Dict[str, Any] = {"role": m.role}
        if m.content is not None:
            msg["content"] = m.content
        elif m.role != "assistant":
            msg["content"] = ""
        if m.tool_call_id:
            msg["tool_call_id"] = m.tool_call_id
        if m.tool_calls:
            msg["tool_calls"] = m.tool_calls
            if "content" not in msg:
                msg["content"] = None
        if m.name:
            msg["name"] = m.name
        messages.append(msg)

    # Attach images to the last user message (multimodal)
    if req.attached_images and messages and messages[-1].get("role") == "user":
        last = messages[-1]
        text = last.get("content", "") or ""
        parts: List[Dict[str, Any]] = [{"type": "text", "text": str(text)}]
        image_ids_used = []
        for img_id in req.attached_images:
            img = get_image(img_id)
            if img:
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{img['mime']};base64,{img['b64']}"},
                })
                image_ids_used.append(img_id)
        if len(parts) > 1:
            last["content"] = parts
            if image_ids_used:
                parts[0]["text"] = (
                    str(text)
                    + f"\n\n[The user attached {len(image_ids_used)} image(s). "
                    f"image_ids: {', '.join(image_ids_used)}. You can see them directly in this message. "
                    f"If needed, use analyze_image tool with one of these ids for a detailed OCR/analysis pass.]"
                ).strip()

    # System prompt — make image_gen invocation crystal clear so the model actually uses it.
    owner_hint = ""
    if user.get("is_owner"):
        owner_hint = (
            " The current user is the OWNER (username admin) of this repository who runs the "
            "GitHub Action. Treat them with priority; they can manage other accounts."
        )

    if not messages or messages[0].get("role") != "system":
        messages.insert(0, {
            "role": "system",
            "content": (
                "You are Kimi, a precise, helpful AI assistant running on GitHub Actions."
                f"{owner_hint}"
                "\n\nYou have four tools:"
                "\n  • web_search(query, max_results) — DuckDuckGo. Use for CURRENT / RECENT facts, news, prices, versions."
                " Do NOT call it more than TWICE per user turn. Do NOT re-issue the same query. If the results are enough, STOP searching and answer."
                "\n  • run_code(language, code) — Python / Bash / HTML sandbox. Use to run/verify snippets, do math, test scripts."
                "\n  • analyze_image(image_id, question) — OCR / detailed vision on an already-uploaded image (use only when needed; you already SEE the image directly)."
                "\n  • generate_image(prompt, width, height) — Create NEW images from a text prompt using Pollinations.ai."
                "\n\nIMPORTANT — image creation:"
                " Whenever the user asks you to CREATE, DRAW, PAINT, MAKE, GENERATE, or PRODUCE an image / picture / illustration / poster / artwork,"
                " you MUST call `generate_image` with a VIVID, DETAILED English prompt (subject, style, lighting, composition, colors, mood)."
                " Do NOT reply with just text; call the tool. After the tool returns a URL, briefly describe what you created."
                "\n\nRules:"
                "\n  - Reply in the SAME language as the user (Persian ↔ English)."
                "\n  - Format code as fenced blocks with a language tag (```python, ```html, ...)."
                "\n  - Do not repeat the same tool call — one search / one code run per intent is enough."
                + mode_cfg["system_extra"]
            ),
        })

    tools = TOOL_DEFINITIONS if req.enable_tools else None

    async def event_generator():
        loop_start = time.monotonic()
        max_iterations = 4  # was 6 — reduced to prevent loops
        final_content = ""
        seen_tool_sigs: Dict[str, int] = {}

        # Heartbeat: emit a comment every ~5s so nginx/proxies never close the SSE.
        try:
            for iteration in range(max_iterations):
                # Send thinking status right away so client shows spinner even if
                # first delta takes a moment (fixes "second message never shows").
                yield sse("thinking", {
                    "status": "start",
                    "elapsed": round(time.monotonic() - loop_start, 2),
                    "iteration": iteration,
                })

                accumulated_content = ""
                tool_calls_buffer: Dict[int, Dict[str, Any]] = {}

                async for chunk in client.chat_stream(
                    messages,
                    tools=tools,
                    temperature=mode_cfg["temperature"],
                    max_tokens=mode_cfg["max_tokens"],
                ):
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {}) or {}
                    if "content" in delta and delta["content"]:
                        accumulated_content += delta["content"]
                        yield sse("delta", {"content": delta["content"]})

                    if "tool_calls" in delta and delta["tool_calls"]:
                        for tc in delta["tool_calls"]:
                            idx = tc.get("index", 0)
                            if idx not in tool_calls_buffer:
                                tool_calls_buffer[idx] = {
                                    "id": tc.get("id", f"call_{iteration}_{idx}"),
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""},
                                }
                            fn = tc.get("function", {}) or {}
                            if fn.get("name"):
                                tool_calls_buffer[idx]["function"]["name"] = fn["name"]
                            if fn.get("arguments"):
                                tool_calls_buffer[idx]["function"]["arguments"] += fn["arguments"]
                            if tc.get("id"):
                                tool_calls_buffer[idx]["id"] = tc["id"]

                final_content = accumulated_content

                # No tool calls -> done
                if not tool_calls_buffer:
                    yield sse("done", {
                        "content": accumulated_content,
                        "elapsed": round(time.monotonic() - loop_start, 2),
                    })
                    return

                # Tool round: append assistant msg with tool_calls
                tool_calls_list = [tool_calls_buffer[i] for i in sorted(tool_calls_buffer.keys())]
                assistant_msg: Dict[str, Any] = {
                    "role": "assistant",
                    "content": accumulated_content if accumulated_content else None,
                    "tool_calls": tool_calls_list,
                }
                messages.append(assistant_msg)

                for tc in tool_calls_list:
                    fn_name = tc["function"]["name"]
                    try:
                        fn_args = json.loads(tc["function"]["arguments"] or "{}")
                    except json.JSONDecodeError:
                        fn_args = {}

                    # De-dupe: if the model calls the same tool with same args, short-circuit.
                    sig = _tool_signature(fn_name, fn_args)
                    seen_tool_sigs[sig] = seen_tool_sigs.get(sig, 0) + 1
                    if seen_tool_sigs[sig] > 1:
                        log.warning("Duplicate tool call skipped: %s", sig)
                        yield sse("tool_call", {"id": tc["id"], "name": fn_name, "args": fn_args, "duplicate": True})
                        result = {"note": "Duplicate call — already ran this exact tool with the same arguments earlier in this turn. Please use the previous result and produce your final answer now."}
                        yield sse("tool_result", {"id": tc["id"], "name": fn_name, "result": result})
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "name": fn_name,
                            "content": json.dumps(result, ensure_ascii=False),
                        })
                        continue

                    yield sse("tool_call", {"id": tc["id"], "name": fn_name, "args": fn_args})
                    result = await execute_tool(fn_name, fn_args)
                    yield sse("tool_result", {"id": tc["id"], "name": fn_name, "result": result})

                    if fn_name == "generate_image" and result.get("ok") and result.get("url"):
                        yield sse("image_generated", {"url": result["url"], "prompt": result.get("prompt", "")})

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "name": fn_name,
                        "content": json.dumps(result, ensure_ascii=False)[:20000],
                    })

                continue

            # Reached max iterations — force a final answer using non-streaming call.
            log.warning("Max tool iterations reached — forcing final answer")
            try:
                messages.append({
                    "role": "system",
                    "content": "You have used all tool budget. Produce your FINAL answer now, in text only. Do not call any more tools.",
                })
                final = await client.chat(messages, tools=None,
                                          temperature=mode_cfg["temperature"],
                                          max_tokens=mode_cfg["max_tokens"])
                msg = (final.get("choices") or [{}])[0].get("message", {})
                content = msg.get("content", "") or ""
                if content:
                    yield sse("delta", {"content": content})
                    final_content = content
            except Exception as e:
                log.warning("Final-answer forcing failed: %s", e)

            yield sse("done", {
                "content": final_content,
                "elapsed": round(time.monotonic() - loop_start, 2),
                "note": "Max tool iterations reached.",
            })

        except KimiAPIError as e:
            log.error("Upstream error: %s %s", e.status, e.body[:300])
            yield sse("error", {
                "message": f"Upstream API error {e.status}: {e.body[:300]}",
                "status": e.status,
            })
        except Exception as e:
            log.exception("Stream error")
            yield sse("error", {"message": f"{type(e).__name__}: {e}"})

    return StreamingResponse(event_generator(), media_type="text/event-stream",
                             headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache",
                                      "Connection": "keep-alive"})


# ---------- Chat persistence (per user) ----------
def _user_chat_dir(username: str) -> Path:
    d = CHATS_DIR / username
    d.mkdir(parents=True, exist_ok=True)
    return d


@app.post("/api/chats/save")
async def save_chat(req: SaveChatRequest, authorization: Optional[str] = Header(None)):
    user = require_user(authorization)
    d = _user_chat_dir(user["username"])
    path = d / f"{req.chat_id}.json"
    path.write_text(json.dumps({
        "id": req.chat_id,
        "title": req.title,
        "messages": req.messages,
        "updated_at": time.time(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "path": str(path.name)}


@app.get("/api/chats")
async def list_chats(authorization: Optional[str] = Header(None)):
    user = require_user(authorization)
    d = _user_chat_dir(user["username"])
    items = []
    for p in sorted(d.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            items.append({
                "id": data.get("id", p.stem),
                "title": data.get("title", "Untitled"),
                "message_count": len(data.get("messages", [])),
                "mtime": p.stat().st_mtime,
            })
        except Exception:
            continue
    return {"chats": items}


@app.get("/api/chats/{chat_id}")
async def get_chat(chat_id: str, authorization: Optional[str] = Header(None)):
    user = require_user(authorization)
    path = _user_chat_dir(user["username"]) / f"{chat_id}.json"
    if not path.exists():
        raise HTTPException(404, "Chat not found")
    return json.loads(path.read_text(encoding="utf-8"))


@app.delete("/api/chats/{chat_id}")
async def delete_chat(chat_id: str, authorization: Optional[str] = Header(None)):
    user = require_user(authorization)
    path = _user_chat_dir(user["username"]) / f"{chat_id}.json"
    if path.exists():
        path.unlink()
    return {"ok": True}


# ---------- File upload ----------
@app.post("/api/upload/image")
async def upload_image(file: UploadFile = File(...), authorization: Optional[str] = Header(None)):
    require_user(authorization)
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "Only image files allowed")
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(400, "Image too large (max 10MB)")
    b64 = base64.b64encode(data).decode("ascii")
    image_id = f"img_{uuid.uuid4().hex[:12]}"
    store_image(image_id, b64, file.content_type)
    # Also persist to disk so it survives restarts (image gets loaded on demand).
    try:
        ext = (file.content_type.split("/")[-1] or "png").split(";")[0]
        (IMAGES_DIR / f"{image_id}.{ext}").write_bytes(data)
        (IMAGES_DIR / f"{image_id}.meta").write_text(file.content_type, encoding="utf-8")
    except Exception as e:
        log.warning("Failed to persist image %s: %s", image_id, e)
    return {"image_id": image_id, "size": len(data), "mime": file.content_type}


@app.post("/api/upload/file")
async def upload_file(file: UploadFile = File(...), authorization: Optional[str] = Header(None)):
    require_user(authorization)
    data = await file.read()
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(400, "File too large (max 5MB)")
    name = file.filename or "file"
    lower = name.lower()
    text = ""
    try:
        if lower.endswith(".pdf"):
            try:
                import PyPDF2, io
                reader = PyPDF2.PdfReader(io.BytesIO(data))
                text = "\n".join((page.extract_text() or "") for page in reader.pages)
            except Exception as e:
                raise HTTPException(400, f"PDF parse error: {e}")
        else:
            text = data.decode("utf-8", errors="replace")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Could not read file: {e}")
    if len(text) > 100000:
        text = text[:100000] + "\n... [truncated]"
    return {"filename": name, "size": len(data), "text": text}


@app.post("/api/generate_image")
async def api_generate_image(req: ImageGenRequest, authorization: Optional[str] = Header(None)):
    require_user(authorization)
    return await generate_image(req.prompt, req.width, req.height)


@app.post("/api/run")
async def api_run(payload: Dict[str, Any], authorization: Optional[str] = Header(None)):
    require_user(authorization)
    return await run_code(payload.get("language", "python"), payload.get("code", ""))


# ---------- Rehydrate on-disk images into in-memory store (survive restarts) ----------
def _rehydrate_images():
    n = 0
    for meta in IMAGES_DIR.glob("*.meta"):
        try:
            image_id = meta.stem
            mime = meta.read_text(encoding="utf-8").strip()
            # Find binary
            for cand in IMAGES_DIR.glob(f"{image_id}.*"):
                if cand.suffix == ".meta":
                    continue
                b64 = base64.b64encode(cand.read_bytes()).decode("ascii")
                store_image(image_id, b64, mime)
                n += 1
                break
        except Exception as e:
            log.warning("rehydrate %s failed: %s", meta, e)
    if n:
        log.info("Rehydrated %d image(s) from disk", n)


_rehydrate_images()


# ---------- Static frontend ----------
@app.get("/")
async def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/login")
async def login_page():
    return FileResponse(FRONTEND_DIR / "login.html")


app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "7860"))
    uvicorn.run("backend.server:app", host="0.0.0.0", port=port, log_level="info")
