"""
Kimi / MiniMax API Client (v2.3)
Handles both regular and streaming chat completions with tool calling support.

Uses a Cloudflare Worker proxy to bypass the anti-bot challenge on the upstream
inference host. The proxy URL is configured via KIMI_BASE_URL.

v2.3 changes
------------
* Multi-model support: Kimi K2.6 AND MiniMax M2.7 (switchable per request).
* Robust 429 handling: exponential backoff with jitter + Retry-After respect,
  so "rate limit exceeded: too many concurrent requests" is retried instead of
  bubbled up to the user.
* `vision()` now auto-selects a vision-capable model when the caller's model
  is text-only, and returns the actual upstream error verbatim so image
  analysis failures are debuggable.
"""
import os
import json
import random
import asyncio
import logging
from typing import AsyncGenerator, List, Dict, Any, Optional

from curl_cffi import requests as cffi_requests

log = logging.getLogger("kimi-client")

DEFAULT_IMPERSONATE = os.getenv("KIMI_IMPERSONATE", "chrome124").strip() or "chrome124"
FALLBACK_IMPERSONATE = "chrome120"

# Retryable HTTP statuses (transient / server-side / rate limit)
_RETRYABLE_STATUSES = {403, 408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524}


class KimiAPIError(RuntimeError):
    """Raised when the upstream API returns an error we couldn't recover from."""
    def __init__(self, status: int, body: str, message: str = ""):
        self.status = status
        self.body = body
        super().__init__(message or f"API error {status}: {body[:400]}")


# ---- Available models --------------------------------------------------------
# Kept as a module-level constant so the /api/models endpoint and the frontend
# picker stay in sync with what the client actually knows how to talk to.
AVAILABLE_MODELS = [
    {
        "id": "moonshotai/Kimi-K2.6",
        "name": "Kimi K2.6",
        "vision": True,
        "provider": "kimi",
    },
    {
        "id": "MiniMaxAI/MiniMax-M2.7",
        "name": "MiniMax M2.7",
        "vision": True,
        "provider": "minimax",
    },
]

# Fast lookup by id
_MODEL_INDEX = {m["id"]: m for m in AVAILABLE_MODELS}

# Default vision-capable model if the currently-selected one is text-only.
_DEFAULT_VISION_MODEL = "moonshotai/Kimi-K2.6"


def _model_meta(model_id: str) -> Dict[str, Any]:
    return _MODEL_INDEX.get(model_id, {"id": model_id, "vision": True, "provider": "kimi"})


# ---- Backoff helper ----------------------------------------------------------
def _parse_retry_after(resp) -> Optional[float]:
    """Read a Retry-After header if present (seconds or HTTP-date). Best-effort."""
    try:
        ra = resp.headers.get("retry-after") or resp.headers.get("Retry-After")
    except Exception:
        ra = None
    if not ra:
        return None
    try:
        return max(0.0, float(ra))
    except Exception:
        return None


def _backoff_delay(attempt: int, status: int, retry_after: Optional[float]) -> float:
    """
    Compute a backoff delay:
      * 429 → longer delays (upstream told us to slow down)
      * 5xx → moderate delays
      * anything else transient → short.
    Adds jitter so multiple concurrent requests desync.
    """
    if retry_after is not None:
        return min(retry_after, 20.0)
    if status == 429:
        base = 2.0 * (2 ** (attempt - 1))  # 2, 4, 8, 16
        base = min(base, 16.0)
    else:
        base = 1.0 * (2 ** (attempt - 1))  # 1, 2, 4, 8
        base = min(base, 8.0)
    return base + random.uniform(0.0, base * 0.4)


class KimiClient:
    def __init__(self, model: Optional[str] = None):
        # ---- credentials ----
        # A single request may be routed to Kimi or MiniMax; we accept keys for
        # both. KIMI_API_KEY stays the primary/legacy env var; MINIMAX_API_KEY
        # falls back to it if not set separately.
        self.api_key = (os.getenv("KIMI_API_KEY") or "").strip()
        self.minimax_api_key = (os.getenv("MINIMAX_API_KEY") or "").strip() or self.api_key

        default_base = "https://kimi-proxy.abol89898.workers.dev/v1"
        self.base_url = os.getenv("KIMI_BASE_URL", default_base).strip().rstrip("/")
        if not self.base_url.endswith("/v1"):
            self.base_url = self.base_url + "/v1"

        # MiniMax may sit behind the same proxy (default) or its own base URL.
        self.minimax_base_url = (os.getenv("MINIMAX_BASE_URL") or self.base_url).strip().rstrip("/")
        if not self.minimax_base_url.endswith("/v1"):
            self.minimax_base_url = self.minimax_base_url + "/v1"

        self.model = (model or os.getenv("KIMI_MODEL", "moonshotai/Kimi-K2.6")).strip()
        self.proxy = os.getenv("KIMI_PROXY", "").strip() or None
        self.impersonate = DEFAULT_IMPERSONATE

        if not self.api_key:
            raise RuntimeError("KIMI_API_KEY is not set")

    # ---- per-model routing ---------------------------------------------------
    def _route_for(self, model_id: str) -> Dict[str, str]:
        """
        Return {base_url, api_key} for the given model. MiniMax gets its own
        api key (falls back to KIMI_API_KEY if not configured separately).
        """
        meta = _model_meta(model_id)
        if meta.get("provider") == "minimax":
            return {
                "base_url": self.minimax_base_url,
                "api_key": self.minimax_api_key or self.api_key,
            }
        return {"base_url": self.base_url, "api_key": self.api_key}

    def _headers(self, api_key: Optional[str] = None) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {api_key or self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        }

    def _proxies(self) -> Optional[Dict[str, str]]:
        if not self.proxy:
            return None
        return {"http": self.proxy, "https": self.proxy}

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        mdl = (model or self.model).strip()
        payload: Dict[str, Any] = {
            "model": mdl,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return await self._post_with_retry(payload, mdl)

    async def _post_with_retry(self, payload: Dict[str, Any], model_id: str) -> Dict[str, Any]:
        route = self._route_for(model_id)
        url = f"{route['base_url']}/chat/completions"
        headers = self._headers(api_key=route["api_key"])

        # Attempt matrix: impersonation profile × payload shape (with/without tools)
        base_attempts = [
            (self.impersonate, payload),
            (FALLBACK_IMPERSONATE, payload),
        ]
        if payload.get("tools"):
            stripped = {k: v for k, v in payload.items() if k not in ("tools", "tool_choice")}
            base_attempts.append((self.impersonate, stripped))

        last_err: Optional[KimiAPIError] = None
        max_backoff_rounds = 4  # for 429 / 5xx we go around this many times

        for round_idx in range(1, max_backoff_rounds + 1):
            for i, (imp, pl) in enumerate(base_attempts, 1):
                try:
                    def _do_request():
                        return cffi_requests.post(
                            url,
                            headers=headers,
                            json=pl,
                            impersonate=imp,
                            timeout=300,
                            proxies=self._proxies(),
                        )

                    resp = await asyncio.to_thread(_do_request)
                    if resp.status_code == 200:
                        return resp.json()

                    body = (resp.text or "")[:800]
                    log.error("POST attempt %d/%d failed: HTTP %d — body=%s",
                              round_idx, i, resp.status_code, body[:200])
                    last_err = KimiAPIError(resp.status_code, body)

                    if resp.status_code not in _RETRYABLE_STATUSES:
                        raise last_err

                    # Rate-limit / transient — sleep before next round.
                    if resp.status_code == 429 or resp.status_code >= 500:
                        delay = _backoff_delay(round_idx, resp.status_code, _parse_retry_after(resp))
                        log.warning("Retryable status %d — sleeping %.1fs before retry",
                                    resp.status_code, delay)
                        await asyncio.sleep(delay)
                        break  # break inner (impersonation) loop → new round with backoff
                except cffi_requests.RequestsError as e:
                    log.error("POST attempt %d/%d transport error: %s", round_idx, i, e)
                    last_err = KimiAPIError(0, str(e), f"transport error: {e}")
                except KimiAPIError:
                    raise

        assert last_err is not None
        raise last_err

    async def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        model: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Streaming chat completion. Bridges blocking curl_cffi → async via a worker thread."""
        import queue, threading

        mdl = (model or self.model).strip()
        route = self._route_for(mdl)
        headers = self._headers(api_key=route["api_key"])
        url = f"{route['base_url']}/chat/completions"

        payload: Dict[str, Any] = {
            "model": mdl,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        base_attempts = [
            (self.impersonate, payload),
            (FALLBACK_IMPERSONATE, payload),
        ]
        if payload.get("tools"):
            stripped = {k: v for k, v in payload.items() if k not in ("tools", "tool_choice")}
            base_attempts.append((self.impersonate, stripped))

        last_err: Optional[KimiAPIError] = None
        max_backoff_rounds = 4

        for round_idx in range(1, max_backoff_rounds + 1):
            round_should_retry = False
            for i, (imp, pl) in enumerate(base_attempts, 1):
                q: "queue.Queue" = queue.Queue(maxsize=1024)
                SENTINEL = object()
                state: Dict[str, Any] = {"error": None, "status": None, "retry_after": None}

                def _worker():
                    try:
                        with cffi_requests.post(
                            url,
                            headers=headers,
                            json=pl,
                            impersonate=imp,
                            timeout=300,
                            stream=True,
                            proxies=self._proxies(),
                        ) as resp:
                            state["status"] = resp.status_code
                            state["retry_after"] = _parse_retry_after(resp)
                            if resp.status_code != 200:
                                body = (resp.text or "")[:800]
                                state["error"] = KimiAPIError(resp.status_code, body)
                                return
                            for line in resp.iter_lines():
                                if not line:
                                    continue
                                if isinstance(line, bytes):
                                    line = line.decode("utf-8", errors="replace")
                                if not line.startswith("data:"):
                                    continue
                                data = line[5:].strip()
                                if data == "[DONE]":
                                    return
                                try:
                                    q.put(json.loads(data))
                                except json.JSONDecodeError:
                                    continue
                    except Exception as e:
                        state["error"] = KimiAPIError(0, str(e), f"transport error: {e}")
                    finally:
                        q.put(SENTINEL)

                thread = threading.Thread(target=_worker, daemon=True)
                thread.start()

                loop = asyncio.get_event_loop()
                got_any = False
                while True:
                    item = await loop.run_in_executor(None, q.get)
                    if item is SENTINEL:
                        break
                    got_any = True
                    yield item

                if state.get("error") is None:
                    return  # clean end
                err: KimiAPIError = state["error"]
                log.error("STREAM attempt %d/%d failed: HTTP %s — body=%s",
                          round_idx, i, err.status, err.body[:200])
                last_err = err

                # Non-retryable → give up
                if err.status and err.status not in _RETRYABLE_STATUSES:
                    raise err
                # Already streamed some content — can't restart cleanly, so bail.
                if got_any:
                    raise err
                # Rate-limit / server error → back off and try again.
                if err.status == 429 or (err.status and err.status >= 500):
                    delay = _backoff_delay(round_idx, err.status, state.get("retry_after"))
                    log.warning("Retryable stream status %d — sleeping %.1fs", err.status, delay)
                    await asyncio.sleep(delay)
                    round_should_retry = True
                    break
            if not round_should_retry:
                break

        # Fall back to non-streaming
        if last_err is not None:
            log.warning("Streaming failed with %s — falling back to non-streaming", last_err.status)
            try:
                non_stream = {k: v for k, v in payload.items() if k != "stream"}
                result = await self._post_with_retry(non_stream, mdl)
                choices = result.get("choices") or []
                if choices:
                    msg = choices[0].get("message", {}) or {}
                    if msg.get("content"):
                        yield {"choices": [{"delta": {"content": msg["content"]}}]}
                    if msg.get("tool_calls"):
                        for idx, tc in enumerate(msg["tool_calls"]):
                            yield {"choices": [{"delta": {
                                "tool_calls": [{
                                    "index": idx,
                                    "id": tc.get("id", f"call_{idx}"),
                                    "function": tc.get("function", {}),
                                }]
                            }}]}
                return
            except KimiAPIError as e:
                last_err = e

        assert last_err is not None
        raise last_err

    async def vision(self, image_b64: str, prompt: str, mime: str = "image/png",
                     model: Optional[str] = None) -> str:
        """
        Analyze an image. Auto-selects a vision-capable model if the caller's
        current model is text-only, so switching to a non-vision model doesn't
        silently break image analysis.
        """
        target = (model or self.model).strip()
        meta = _model_meta(target)
        if not meta.get("vision"):
            # Fallback to the default vision model instead of failing.
            target = _DEFAULT_VISION_MODEL
            log.info("Model %s is not vision-capable — using %s for vision call",
                     self.model, target)

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt or "Describe this image in detail."},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
                ],
            }
        ]
        try:
            result = await self.chat(messages, temperature=0.3, max_tokens=1024, model=target)
            content = ((result.get("choices") or [{}])[0].get("message", {}) or {}).get("content")
            if not content:
                return "[Vision error] Model returned no content"
            return content
        except KimiAPIError as e:
            # Surface the real body so image-analysis errors are debuggable
            # (previously users saw a silent failure).
            return f"[Vision error {e.status}] {e.body[:300]}"
        except Exception as e:
            return f"[Vision error] {type(e).__name__}: {e}"

    async def ping(self, model: Optional[str] = None) -> Dict[str, Any]:
        mdl = (model or self.model).strip()
        route = self._route_for(mdl)
        payload = {
            "model": mdl,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 8,
            "temperature": 0.0,
        }
        try:
            result = await self._post_with_retry(payload, mdl)
            return {
                "ok": True,
                "model": mdl,
                "base_url": route["base_url"],
                "reply": (result.get("choices") or [{}])[0].get("message", {}).get("content", ""),
            }
        except KimiAPIError as e:
            return {"ok": False, "status": e.status, "body": e.body[:400]}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
