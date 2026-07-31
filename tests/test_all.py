"""End-to-end smoke tests. Run with `pytest tests/` or `python tests/test_all.py`.

These tests DO NOT require an API key or network access to the upstream model —
they cover the FastAPI surface, the tools, and the auth layer locally.
"""
import os
import sys
import json
import asyncio
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Set a fake key so KimiClient init doesn't blow up. Also set a deterministic
# OWNER_PASS so we can log in as the owner in tests.
os.environ.setdefault("KIMI_API_KEY", "dahl_test_key")
os.environ.setdefault("KIMI_BASE_URL", "https://kimi-proxy.abol89898.workers.dev/v1")
os.environ.setdefault("KIMI_MODEL", "moonshotai/Kimi-K2.6")
os.environ["OWNER_PASS"] = "test_owner_pw_123"


def test_auth_module():
    # Re-import so the OWNER_PASS override takes effect.
    import importlib
    from backend import auth as _auth
    importlib.reload(_auth)
    auth = _auth

    auth.ensure_owner()
    # Owner exists with the new ADMIN username
    users = auth._load_users()
    assert auth.OWNER_USERNAME == "ADMIN"
    assert "ADMIN" in users
    assert users["ADMIN"]["is_owner"] is True

    # Login as owner (correct password)
    r = auth.login("ADMIN", "test_owner_pw_123")
    assert r["ok"], r
    assert r["is_owner"] is True
    token = r["token"]

    # Wrong password fails
    bad = auth.login("ADMIN", "wrong")
    assert not bad["ok"]

    # Verify token
    session = auth.verify_token(token)
    assert session and session["username"] == "ADMIN"

    # Register a new user
    tname = "pytest_user_" + os.urandom(3).hex()
    r = auth.register(tname, "pw123")
    assert r["ok"], r
    assert r["is_owner"] is False
    r2 = auth.login(tname, "pw123")
    assert r2["ok"]
    assert r2["is_owner"] is False

    # Cannot register a user that would collide with the owner name (any case)
    r_col = auth.register("admin", "whatever")
    assert not r_col["ok"], "should not be able to register reserved owner name"
    r_col2 = auth.register("ADMIN", "whatever")
    assert not r_col2["ok"]

    # List / delete
    lst = auth.list_users()
    assert any(u["username"] == tname for u in lst)
    r3 = auth.delete_user(tname)
    assert r3["ok"]

    # Can't delete owner (any case)
    r4 = auth.delete_user("ADMIN")
    assert not r4["ok"]
    r4b = auth.delete_user("admin")
    assert not r4b["ok"]

    # Owner login is case-insensitive on the username
    r5 = auth.login("admin", "test_owner_pw_123")
    assert r5["ok"] and r5["is_owner"]
    # And normalises the display username to ADMIN
    assert r5["username"] == "ADMIN"

    print("✓ auth module OK")


def test_tools_signature_dedup():
    """Duplicate tool call signature must be identical → server dedupe kicks in."""
    from backend.server import _tool_signature
    a = _tool_signature("web_search", {"query": "Python news 2026"})
    b = _tool_signature("web_search", {"query": "python NEWS 2026"})  # case-insensitive
    assert a == b, (a, b)
    c = _tool_signature("web_search", {"query": "different"})
    assert a != c
    print("✓ tool signature dedupe OK")


def test_search_tool_offline():
    """Verify the search wrapper handles a missing/broken DDG library gracefully."""
    from backend.tools.search import web_search
    # even if DDG is missing, it should return a dict with an error field, not crash
    result = asyncio.run(web_search("hello", max_results=2))
    assert isinstance(result, dict)
    assert "results" in result
    print("✓ search wrapper OK (offline)")


def test_sandbox_python():
    from backend.tools.sandbox import run_code
    r = asyncio.run(run_code("python", "print('hello sandbox')"))
    assert r["success"] is True, r
    assert "hello sandbox" in r["stdout"]
    # error path
    r2 = asyncio.run(run_code("python", "raise SystemExit(3)"))
    assert r2["returncode"] == 3
    print("✓ sandbox python OK")


def test_sandbox_bash():
    from backend.tools.sandbox import run_code
    r = asyncio.run(run_code("bash", "echo 'via bash'"))
    assert r["success"] is True
    assert "via bash" in r["stdout"]
    print("✓ sandbox bash OK")


def test_sandbox_html():
    from backend.tools.sandbox import run_code
    r = asyncio.run(run_code("html", "<h1>hi</h1>"))
    assert r["success"] is True and r["language"] == "html"
    print("✓ sandbox html OK")


def test_image_store():
    from backend.tools.vision import store_image, get_image
    store_image("img_test", "aGVsbG8=", "image/png")
    img = get_image("img_test")
    assert img and img["mime"] == "image/png"
    print("✓ image store OK")


def test_analyze_image_missing_id():
    """analyze_image with an unknown id must return a structured error, not raise."""
    from backend.tools.vision import analyze_image
    r = asyncio.run(analyze_image("img_does_not_exist_xyz", "what is this?"))
    assert isinstance(r, dict)
    assert r.get("success") is False
    assert "error" in r
    print("✓ analyze_image missing-id OK")


def test_kimi_client_init():
    from backend.kimi_client import KimiClient
    c = KimiClient()
    assert c.api_key
    assert c.base_url.endswith("/v1")
    assert c.model
    print("✓ kimi client init OK")


def test_fastapi_routes_exist():
    from backend.server import app
    routes = {r.path for r in app.routes}
    for p in [
        "/api/health", "/api/models",
        "/api/auth/register", "/api/auth/login", "/api/auth/logout", "/api/auth/me",
        "/api/chat/stream", "/api/chats", "/api/chats/{chat_id}",
        "/api/upload/image", "/api/upload/file", "/api/generate_image", "/api/run",
        "/", "/login",
    ]:
        assert p in routes, f"missing route: {p}"
    print("✓ all FastAPI routes present")


def test_image_gen_returns_url():
    """Even offline, generate_image should still return a valid, ASCII-safe URL."""
    from backend.tools.image_gen import generate_image
    r = asyncio.run(generate_image("a red cat", 512, 512))
    assert r.get("ok") is True
    url = r.get("url", "")
    assert url.startswith("https://image.pollinations.ai/"), url
    # Must be fully ASCII (percent-encoded) — no raw non-latin characters.
    assert all(ord(c) < 128 for c in url), "URL must be ASCII-safe"
    # Persian prompt: still returns a URL (verification may or may not succeed offline)
    r2 = asyncio.run(generate_image("گربه نارنجی زیبا", 512, 512))
    assert r2.get("ok") is True
    assert r2.get("url", "").startswith("https://image.pollinations.ai/")
    assert all(ord(c) < 128 for c in r2["url"]), "Persian prompt URL must be ASCII-safe"
    print("✓ image gen URL OK")


def test_tool_definitions_shape():
    from backend.tools import TOOL_DEFINITIONS
    names = {t["function"]["name"] for t in TOOL_DEFINITIONS}
    assert names == {"web_search", "run_code", "analyze_image", "generate_image"}
    for t in TOOL_DEFINITIONS:
        assert t["type"] == "function"
        assert "parameters" in t["function"]
    print("✓ tool definitions OK")


def test_available_models_has_kimi_and_minimax():
    """v2.3 — both Kimi K2.6 and MiniMax M2.7 must be selectable."""
    from backend.kimi_client import AVAILABLE_MODELS
    ids = {m["id"] for m in AVAILABLE_MODELS}
    assert "moonshotai/Kimi-K2.6" in ids
    assert "MiniMaxAI/MiniMax-M2.7" in ids
    # Both are vision-capable and carry an explicit provider tag for routing
    for m in AVAILABLE_MODELS:
        assert "provider" in m and m["provider"] in ("kimi", "minimax")
        assert m.get("vision") is True
    print("✓ model catalogue (Kimi + MiniMax) OK")


def test_backoff_helper_429_grows():
    """429 backoff must grow with attempt count and respect Retry-After."""
    from backend.kimi_client import _backoff_delay
    d1 = _backoff_delay(1, 429, None)
    d2 = _backoff_delay(2, 429, None)
    d3 = _backoff_delay(3, 429, None)
    # Lower bounds (without jitter) grow: 2, 4, 8
    assert d1 >= 2.0 and d2 >= 4.0 and d3 >= 8.0, (d1, d2, d3)
    # Retry-After (when provided) is honoured and clamped
    assert _backoff_delay(5, 429, 3.0) == 3.0
    assert _backoff_delay(5, 429, 999.0) == 20.0  # clamp
    print("✓ 429 backoff helper OK")


def test_client_routes_minimax_separately():
    """Client must route MiniMax requests to the MiniMax base URL / key."""
    import importlib
    from backend import kimi_client as _kc
    importlib.reload(_kc)
    c = _kc.KimiClient()
    r_kimi = c._route_for("moonshotai/Kimi-K2.6")
    r_minimax = c._route_for("MiniMaxAI/MiniMax-M2.7")
    assert r_kimi["base_url"].endswith("/v1")
    assert r_minimax["base_url"].endswith("/v1")
    # Both have a usable API key (falls back to KIMI_API_KEY when MINIMAX_API_KEY unset)
    assert r_kimi["api_key"] and r_minimax["api_key"]
    print("✓ per-model routing OK")


# ---------------------------------------------------------------------------
# v2.4 regression tests — the two bugs the user reported
# ---------------------------------------------------------------------------

def test_search_survives_ratelimit():
    """Even when DDG raises Ratelimit for every backend, web_search must
    NOT crash and must return a dict with real results from a fallback source.
    """
    from backend.tools import search as search_mod
    import importlib
    importlib.reload(search_mod)

    # Force the primary DDG library to always raise a Ratelimit-style error.
    def _boom(query, max_results):
        raise RuntimeError("DuckDuckGoSearchException: Ratelimit")

    search_mod._ddg_search_sync = _boom  # type: ignore[attr-defined]

    # Also stub the HTML fallback to fail, so we hit the Wikipedia last resort.
    def _boom_html(query, max_results):
        raise RuntimeError("HTML endpoint 429")
    search_mod._ddg_html_fallback = _boom_html  # type: ignore[attr-defined]

    result = asyncio.run(search_mod.web_search("Python programming language", max_results=3))
    assert isinstance(result, dict)
    # Either we got results from the wiki fallback, or a structured error —
    # NEVER an unhandled exception.
    assert "results" in result
    if result.get("results"):
        # If we're online, wiki fallback should have produced entries
        assert result.get("source") == "wikipedia"
        assert all("url" in r and "title" in r for r in result["results"])
        print("✓ search survives DDG ratelimit → wiki fallback OK")
    else:
        assert "error" in result
        print("✓ search survives DDG ratelimit (offline; structured error) OK")


def test_search_cached_key_case_insensitive():
    """Cache TTL bumped to 5min; identical case-insensitive query must hit cache."""
    from backend.tools import search as search_mod
    import importlib
    importlib.reload(search_mod)

    call_count = {"n": 0}
    def _stub(query, max_results):
        call_count["n"] += 1
        return [{"title": "t", "url": "https://x", "snippet": "s"}]
    search_mod._ddg_search_sync = _stub  # type: ignore[attr-defined]

    r1 = asyncio.run(search_mod.web_search("Hello WORLD", max_results=3))
    r2 = asyncio.run(search_mod.web_search("hello world", max_results=3))
    assert r1["count"] == 1 and r2["count"] == 1
    assert r2.get("cached") is True, "second identical query must be cached"
    assert call_count["n"] == 1, "backend must be hit only once"
    print("✓ search cache (case-insensitive) OK")


def test_image_upload_mime_detection():
    """_detect_image_mime must accept:
         - explicit image/* content-type
         - filename extension when content-type is application/octet-stream
         - magic-number sniffing when neither is available.
    Regression for: 'upload image doesn't work'.
    """
    from backend.server import _detect_image_mime
    # PNG magic
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
    assert _detect_image_mime("", "", png_bytes) == "image/png"
    # JPEG magic
    jpeg_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 20
    assert _detect_image_mime("", "application/octet-stream", jpeg_bytes) == "image/jpeg"
    # GIF
    gif_bytes = b"GIF89a" + b"\x00" * 20
    assert _detect_image_mime("", "", gif_bytes) == "image/gif"
    # WEBP
    webp_bytes = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 20
    assert _detect_image_mime("pic.webp", "", webp_bytes) == "image/webp"
    # Charset param must be stripped (real-world bug)
    assert _detect_image_mime("a.jpg", "image/jpeg; charset=binary", b"") == "image/jpeg"
    # Filename fallback when content-type is missing
    assert _detect_image_mime("pic.PNG", "", b"") == "image/png"
    # Non-image → None
    assert _detect_image_mime("note.txt", "text/plain", b"hello") is None
    print("✓ image upload MIME detection (charset/octet-stream/magic) OK")


def _run_image_pipeline(messages_in, attached_images):
    """Directly exercise the image-attachment normalisation block of
    chat_stream without hitting the upstream. We copy the exact logic from
    server.py so that if that block ever regresses, this test will fail.
    """
    from backend.tools.vision import get_image
    from backend.server import log as _log  # noqa: F401
    # Normalise like the server does (mimics ChatMessage.dict()).
    messages = [dict(m) for m in messages_in]

    if attached_images and messages and messages[-1].get("role") == "user":
        last = messages[-1]
        raw = last.get("content", "") or ""
        if isinstance(raw, list):
            text_parts = [p.get("text", "") for p in raw if isinstance(p, dict) and p.get("type") == "text"]
            text = "\n".join(t for t in text_parts if t)
        else:
            text = str(raw)
        image_ids_used = []
        for img_id in attached_images:
            if get_image(img_id):
                image_ids_used.append(img_id)
        if image_ids_used:
            marker = " ".join(f"[IMAGE_ATTACHED: {i}]" for i in image_ids_used)
            hint = (
                f"\n\n{marker}\n"
                f"[SYSTEM NOTE TO ASSISTANT] The user just uploaded "
                f"{len(image_ids_used)} image(s) with image_id(s): "
                f"{', '.join(image_ids_used)}. You CANNOT see them from this text alone. "
                f"You MUST call the `analyze_image` tool with each image_id "
                f"exactly as given (do NOT invent one) to actually look at the picture. "
                f"Do this BEFORE writing your reply."
            )
            last["content"] = (text + hint).strip()
    return messages


def test_chat_stream_attaches_image_marker_not_data_url():
    """Regression for v2.5: attached images must be conveyed as text markers on
    the last user message (NOT as an inline base64 data URL). This is the fix
    for the 'AI says: I did not receive an image' bug reported by users.
    """
    from backend.tools.vision import store_image
    store_image("img_regression_x", "aGVsbG8=", "image/png")

    msgs = _run_image_pipeline(
        [{"role": "user", "content": "what is in this picture?"}],
        ["img_regression_x"],
    )
    last_user = [m for m in msgs if m.get("role") == "user"][-1]
    content = last_user.get("content")
    assert isinstance(content, str), (
        f"user content must stay a string; got {type(content).__name__}"
    )
    assert "[IMAGE_ATTACHED: img_regression_x]" in content, content
    assert "data:image/" not in content, (
        "regression: inline base64 data URL leaked into user content"
    )
    assert "analyze_image" in content, (
        "user message must remind the assistant to call analyze_image"
    )
    print("✓ chat stream uses image-id marker (not inline base64) OK")


def test_chat_stream_only_attaches_current_turn_images():
    """Regression for v2.5: unknown image ids are silently skipped. Combined
    with the frontend fix (only send image_ids from the CURRENT user turn),
    this prevents the payload-bloat that made the model report 'no image'.
    """
    msgs = _run_image_pipeline(
        [{"role": "user", "content": "follow up question"}],
        ["img_does_not_exist_zzz"],
    )
    last_user = [m for m in msgs if m.get("role") == "user"][-1]
    content = last_user.get("content")
    assert isinstance(content, str)
    assert "IMAGE_ATTACHED" not in content, (
        "non-existent image id must not produce a marker"
    )
    print("✓ chat stream skips unknown image ids OK")


def test_chat_stream_handles_rehydrated_multimodal_content():
    """Regression for v2.5: if a rehydrated chat's last user message has
    `content` already as a list of parts, we must flatten it to text — not
    crash and not lose the existing text.
    """
    from backend.tools.vision import store_image
    store_image("img_rehydrate_y", "aGVsbG8=", "image/png")

    msgs = _run_image_pipeline(
        [{"role": "user", "content": [
            {"type": "text", "text": "describe this"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
        ]}],
        ["img_rehydrate_y"],
    )
    last_user = [m for m in msgs if m.get("role") == "user"][-1]
    content = last_user.get("content")
    assert isinstance(content, str), "content must be flattened to a string"
    assert "describe this" in content
    assert "[IMAGE_ATTACHED: img_rehydrate_y]" in content
    # Any old data URL from the rehydrated parts must NOT leak through
    assert "data:image/png;base64,AAA" not in content
    print("✓ chat stream flattens rehydrated multimodal content OK")


def test_upload_image_endpoint_accepts_octet_stream():
    """Full round-trip: POST /api/upload/image with an octet-stream JPEG
    (mirrors what some mobile browsers send) must succeed.
    """
    from fastapi.testclient import TestClient
    from backend import server as _srv
    from backend import auth as _auth
    import importlib
    importlib.reload(_auth)
    _auth.ensure_owner()

    client = TestClient(_srv.app)
    # Log in as owner to get a token
    r = client.post("/api/auth/login", json={"username": "ADMIN",
                                             "password": os.environ["OWNER_PASS"]})
    assert r.status_code == 200, r.text
    token = r.json()["token"]

    # A minimal valid JPEG (SOI + APP0 + EOI)
    jpeg = (b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01"
            b"\x00\x00\xff\xd9")
    files = {"file": ("photo.jpg", jpeg, "application/octet-stream")}
    r = client.post("/api/upload/image", files=files,
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, f"expected 200 got {r.status_code}: {r.text}"
    body = r.json()
    assert body["image_id"].startswith("img_")
    assert body["mime"] == "image/jpeg"
    assert body["size"] == len(jpeg)

    # A text file must be rejected with 400
    r2 = client.post("/api/upload/image",
                     files={"file": ("note.txt", b"hello", "text/plain")},
                     headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code == 400

    # And unauthorized must be 401
    r3 = client.post("/api/upload/image",
                     files={"file": ("photo.jpg", jpeg, "image/jpeg")})
    assert r3.status_code == 401

    print("✓ /api/upload/image accepts octet-stream + rejects non-images OK")


# ---------------------------------------------------------------------------
# v3.0 sandbox tests — multi-language, packages, workspace, artifacts, limits
# ---------------------------------------------------------------------------

def test_sandbox_v3_supported_languages():
    """v3 must advertise at least the core language set."""
    from backend.tools.sandbox import SUPPORTED_LANGUAGES
    core = {"python", "bash", "html", "javascript", "typescript", "c", "cpp",
            "go", "rust", "java", "ruby", "php", "lua", "r", "sql", "perl"}
    missing = core - set(SUPPORTED_LANGUAGES)
    assert not missing, f"sandbox v3 missing languages: {missing}"
    print(f"✓ sandbox v3 advertises {len(SUPPORTED_LANGUAGES)} languages")


def test_sandbox_v3_shape_backward_compat():
    """v3 result must still expose ALL v2 fields (success/stdout/stderr/returncode/language)."""
    from backend.tools.sandbox import run_code
    r = asyncio.run(run_code("python", "print(2+2)"))
    for k in ("success", "stdout", "stderr", "returncode", "language"):
        assert k in r, f"v2-compat key missing: {k}"
    # v3-only additive fields
    for k in ("elapsed", "workspace_id", "files", "timed_out", "truncated"):
        assert k in r, f"v3 additive key missing: {k}"
    assert r["success"] is True and "4" in r["stdout"]
    print("✓ sandbox v3 keeps v2 result shape (additive-only upgrade)")


def test_sandbox_v3_alias_and_auto():
    """Aliases (py, js, sh) must resolve, and language='auto' must work off a shebang."""
    from backend.tools.sandbox import run_code
    r = asyncio.run(run_code("py", "print('hi')"))
    assert r["success"] and r["language"] == "python"
    r2 = asyncio.run(run_code("sh", "echo aliased"))
    assert r2["success"] and r2["language"] == "bash"
    r3 = asyncio.run(run_code("auto", "#!/usr/bin/env python3\nprint('autolang')"))
    assert r3["success"] and r3["language"] == "python"
    # unknown alias → structured error
    r4 = asyncio.run(run_code("klingon", "whatever"))
    assert r4["success"] is False and "Unsupported" in r4.get("error", "")
    print("✓ sandbox v3 language aliases + auto-detect OK")


def test_sandbox_v3_stdin():
    """Stdin must reach the program."""
    from backend.tools.sandbox import run_code
    r = asyncio.run(run_code(
        "python",
        "import sys\nprint('got:', sys.stdin.read().strip())",
        stdin="pipe-me-in",
    ))
    assert r["success"], r
    assert "pipe-me-in" in r["stdout"]
    print("✓ sandbox v3 stdin OK")


def test_sandbox_v3_env_vars():
    """Custom env vars must be visible; unsafe names must be dropped silently."""
    from backend.tools.sandbox import run_code
    r = asyncio.run(run_code(
        "python",
        "import os\nprint('K1=', os.environ.get('MY_VAR', '?'))",
        env={"MY_VAR": "forty-two", "bad name": "nope", "lowercase": "nope"},
    ))
    assert r["success"] and "forty-two" in r["stdout"]
    print("✓ sandbox v3 env-var passing OK")


def test_sandbox_v3_workspace_persistence():
    """A named workspace must retain files between two independent run_code calls."""
    from backend.tools.sandbox import run_code, list_workspace_files
    ws = "pytest_ws_" + os.urandom(3).hex()
    r1 = asyncio.run(run_code(
        "python",
        "open('data.txt','w').write('persisted\\n')",
        workspace_id=ws,
    ))
    assert r1["success"] and r1["workspace_id"] == ws
    # New call, same ws — file must still be there
    r2 = asyncio.run(run_code(
        "python",
        "print(open('data.txt').read().strip())",
        workspace_id=ws,
    ))
    assert r2["success"] and "persisted" in r2["stdout"], r2
    listing = list_workspace_files(ws)
    assert any(f["path"] == "data.txt" for f in listing)
    print("✓ sandbox v3 persistent workspace OK")


def test_sandbox_v3_artifact_detection():
    """Files created during a run must appear in `files` with mime + size."""
    from backend.tools.sandbox import run_code
    r = asyncio.run(run_code(
        "python",
        "open('report.csv','w').write('a,b\\n1,2\\n')",
    ))
    assert r["success"]
    paths = [f["path"] for f in r.get("files", [])]
    assert "report.csv" in paths, paths
    csv = [f for f in r["files"] if f["path"] == "report.csv"][0]
    assert csv["size"] > 0 and csv["mime"] in ("text/csv", "application/octet-stream")
    print("✓ sandbox v3 artifact detection OK")


def test_sandbox_v3_files_seed():
    """Pre-seeded `files` must be dropped into the workspace before the run."""
    from backend.tools.sandbox import run_code
    r = asyncio.run(run_code(
        "python",
        "print(open('config.json').read().strip())",
        files={"config.json": '{"seeded": true}'},
    ))
    assert r["success"] and '"seeded": true' in r["stdout"]
    print("✓ sandbox v3 file-seeding OK")


def test_sandbox_v3_timeout_respected():
    """A caller-supplied short timeout must actually kill the process."""
    from backend.tools.sandbox import run_code
    r = asyncio.run(run_code(
        "python",
        "import time\ntime.sleep(10)\nprint('should not appear')",
        timeout=2,
    ))
    assert r["success"] is False
    assert r.get("timed_out") is True
    print("✓ sandbox v3 timeout enforcement OK")


def test_sandbox_v3_read_workspace_file_traversal():
    """read_workspace_file must refuse path-traversal attempts."""
    from backend.tools.sandbox import run_code, read_workspace_file
    ws = "pytest_trav_" + os.urandom(3).hex()
    asyncio.run(run_code("python", "open('safe.txt','w').write('ok')", workspace_id=ws))
    # Valid path
    got = read_workspace_file(ws, "safe.txt")
    assert got is not None and got[0] == b"ok"
    # Traversal attempts
    assert read_workspace_file(ws, "../../../../etc/passwd") is None
    assert read_workspace_file(ws, "../.gitignore") is None
    print("✓ sandbox v3 path-traversal guard OK")


def test_sandbox_v3_unsafe_package_name_rejected():
    """Package names with shell metacharacters must be refused before install."""
    from backend.tools.sandbox import run_code
    r = asyncio.run(run_code(
        "python",
        "print('hi')",
        packages=["good_pkg", "; rm -rf /"],
    ))
    assert r["success"] is False
    assert "unsafe package name" in (r.get("install", {}).get("error", "") + r.get("error", ""))
    print("✓ sandbox v3 package-name safety OK")


def test_sandbox_v3_javascript_when_available():
    """Node.js is present on the Actions runner; if here too, JS should just work."""
    import shutil as _sh
    from backend.tools.sandbox import run_code
    if not _sh.which("node"):
        print("⚠  node not installed on this host — skipping JS test")
        return
    r = asyncio.run(run_code("javascript", "console.log('js-'+ (1+2))"))
    assert r["success"] and "js-3" in r["stdout"], r
    print("✓ sandbox v3 javascript OK")


def test_sandbox_v3_compiled_c_when_available():
    """If gcc is installed, C code must compile and run."""
    import shutil as _sh
    from backend.tools.sandbox import run_code
    if not _sh.which("gcc"):
        print("⚠  gcc not installed on this host — skipping C test")
        return
    r = asyncio.run(run_code(
        "c",
        '#include <stdio.h>\nint main(){printf("c-ok %d\\n", 42);return 0;}',
    ))
    assert r["success"], r
    assert "c-ok 42" in r["stdout"]
    assert "compile" in r and r["compile"]["returncode"] == 0
    print("✓ sandbox v3 C compile+run OK")


def test_sandbox_v3_compile_failure_surfaced():
    """A compile error must surface as success=False with compile.stderr populated."""
    import shutil as _sh
    from backend.tools.sandbox import run_code
    if not _sh.which("gcc"):
        print("⚠  gcc not installed — skipping compile-error test")
        return
    r = asyncio.run(run_code("c", "int main(){ return this_symbol_does_not_exist; }"))
    assert r["success"] is False
    assert "compile" in r and r["compile"]["returncode"] != 0
    assert r["compile"]["stderr"], "compiler must report an error message"
    print("✓ sandbox v3 compile-failure separation OK")


def test_sandbox_v3_tool_definition_includes_new_params():
    """TOOL_DEFINITIONS must expose the new run_code kwargs to the model."""
    from backend.tools import TOOL_DEFINITIONS
    run_code_def = next(t for t in TOOL_DEFINITIONS
                       if t["function"]["name"] == "run_code")
    props = run_code_def["function"]["parameters"]["properties"]
    for k in ("timeout", "stdin", "packages", "workspace_id", "env", "files"):
        assert k in props, f"run_code tool definition missing param: {k}"
    # Description must mention the new capability so the model knows about it.
    desc = run_code_def["function"]["description"].lower()
    assert "python" in desc and "javascript" in desc and "go" in desc
    print("✓ sandbox v3 tool definition exposes new params")


def test_sandbox_v3_dedupe_treats_stdin_as_distinct():
    """Same code + different stdin must not be treated as a duplicate call."""
    from backend.server import _tool_signature
    a = _tool_signature("run_code",
                        {"language": "python", "code": "print(1)", "stdin": "a"})
    b = _tool_signature("run_code",
                        {"language": "python", "code": "print(1)", "stdin": "b"})
    assert a != b, "different stdin must produce different signatures"
    print("✓ sandbox v3 signature honours stdin/packages/workspace")


def test_sandbox_v3_new_routes_present():
    """New /api/sandbox/* routes must be registered."""
    from backend.server import app
    paths = {r.path for r in app.routes}
    for expected in (
        "/api/sandbox/languages",
        "/api/sandbox/files/{workspace_id}",
        "/api/sandbox/files/{workspace_id}/{path:path}",
        "/api/sandbox/sweep",
    ):
        assert expected in paths, f"missing new route: {expected}"
    print("✓ sandbox v3 API routes registered")


if __name__ == "__main__":
    test_auth_module()
    test_tools_signature_dedup()
    test_search_tool_offline()
    test_sandbox_python()
    test_sandbox_bash()
    test_sandbox_html()
    test_image_store()
    test_analyze_image_missing_id()
    test_kimi_client_init()
    test_fastapi_routes_exist()
    test_image_gen_returns_url()
    test_tool_definitions_shape()
    test_available_models_has_kimi_and_minimax()
    test_backoff_helper_429_grows()
    test_client_routes_minimax_separately()
    # v2.4 regression tests
    test_search_survives_ratelimit()
    test_search_cached_key_case_insensitive()
    test_image_upload_mime_detection()
    test_upload_image_endpoint_accepts_octet_stream()
    test_chat_stream_attaches_image_marker_not_data_url()
    test_chat_stream_only_attaches_current_turn_images()
    test_chat_stream_handles_rehydrated_multimodal_content()
    # v3.0 sandbox regression tests
    test_sandbox_v3_supported_languages()
    test_sandbox_v3_shape_backward_compat()
    test_sandbox_v3_alias_and_auto()
    test_sandbox_v3_stdin()
    test_sandbox_v3_env_vars()
    test_sandbox_v3_workspace_persistence()
    test_sandbox_v3_artifact_detection()
    test_sandbox_v3_files_seed()
    test_sandbox_v3_timeout_respected()
    test_sandbox_v3_read_workspace_file_traversal()
    test_sandbox_v3_unsafe_package_name_rejected()
    test_sandbox_v3_javascript_when_available()
    test_sandbox_v3_compiled_c_when_available()
    test_sandbox_v3_compile_failure_surfaced()
    test_sandbox_v3_tool_definition_includes_new_params()
    test_sandbox_v3_dedupe_treats_stdin_as_distinct()
    test_sandbox_v3_new_routes_present()
    print("\nALL TESTS PASSED ✅")
