"""
Kimi / MiniMax / GLM API Client
Handles both regular and streaming chat completions with tool calling support.

Uses a Cloudflare Worker proxy to bypass the anti-bot challenge on the upstream
inference host. The proxy URL is configured via KIMI_BASE_URL.
"""
import os
import json
import logging
from typing import AsyncGenerator, List, Dict, Any, Optional

from curl_cffi import requests as cffi_requests

log = logging.getLogger("kimi-client")

DEFAULT_IMPERSONATE = os.getenv("KIMI_IMPERSONATE", "chrome124").strip() or "chrome124"
FALLBACK_IMPERSONATE = "chrome120"


class KimiAPIError(RuntimeError):
    """Raised when the upstream API returns an error we couldn't recover from."""
    def __init__(self, status: int, body: str, message: str = ""):
        self.status = status
        self.body = body
        super().__init__(message or f"API error {status}: {body[:400]}")


# Available models
AVAILABLE_MODELS = [
    {"id": "moonshotai/Kimi-K2.6", "name": "Kimi K2.6", "vision": True},
]


class KimiClient:
    def __init__(self, model: Optional[str] = None):
        self.api_key = os.getenv("KIMI_API_KEY", "").strip()
        default_base = "https://kimi-proxy.abol89898.workers.dev/v1"
        self.base_url = os.getenv("KIMI_BASE_URL", default_base).strip().rstrip("/")
        # Ensure /v1 suffix
        if not self.base_url.endswith("/v1"):
            self.base_url = self.base_url + "/v1"
        self.model = (model or os.getenv("KIMI_MODEL", "moonshotai/Kimi-K2.6")).strip()
        self.proxy = os.getenv("KIMI_PROXY", "").strip() or None
        self.impersonate = DEFAULT_IMPERSONATE
        if not self.api_key:
            raise RuntimeError("KIMI_API_KEY is not set")

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
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
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return await self._post_with_retry(payload)

    async def _post_with_retry(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}/chat/completions"

        attempts = [
            (self.impersonate, payload),
            (FALLBACK_IMPERSONATE, payload),
        ]
        if payload.get("tools"):
            stripped = {k: v for k, v in payload.items() if k not in ("tools", "tool_choice")}
            attempts.append((self.impersonate, stripped))

        last_err: Optional[KimiAPIError] = None
        import asyncio

        for i, (imp, pl) in enumerate(attempts, 1):
            try:
                def _do_request():
                    return cffi_requests.post(
                        url,
                        headers=self._headers(),
                        json=pl,
                        impersonate=imp,
                        timeout=300,
                        proxies=self._proxies(),
                    )

                resp = await asyncio.to_thread(_do_request)
                if resp.status_code == 200:
                    return resp.json()

                body = (resp.text or "")[:800]
                log.error("POST attempt %d failed: HTTP %d — body=%s", i, resp.status_code, body)
                last_err = KimiAPIError(resp.status_code, body)
                if resp.status_code not in (403, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524):
                    raise last_err
            except cffi_requests.RequestsError as e:
                log.error("POST attempt %d transport error: %s", i, e)
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
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Streaming chat completion. Bridges blocking curl_cffi → async via a worker thread."""
        import asyncio, queue, threading

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        url = f"{self.base_url}/chat/completions"
        attempts = [
            (self.impersonate, payload),
            (FALLBACK_IMPERSONATE, payload),
        ]
        if payload.get("tools"):
            stripped = {k: v for k, v in payload.items() if k not in ("tools", "tool_choice")}
            attempts.append((self.impersonate, stripped))

        last_err: Optional[KimiAPIError] = None
        for i, (imp, pl) in enumerate(attempts, 1):
            q: "queue.Queue" = queue.Queue(maxsize=1024)
            SENTINEL = object()
            state: Dict[str, Any] = {"error": None, "status": None}

            def _worker():
                try:
                    with cffi_requests.post(
                        url,
                        headers=self._headers(),
                        json=pl,
                        impersonate=imp,
                        timeout=300,
                        stream=True,
                        proxies=self._proxies(),
                    ) as resp:
                        state["status"] = resp.status_code
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
            log.error("STREAM attempt %d failed: HTTP %s — body=%s", i, err.status, err.body[:300])
            last_err = err
            if err.status and err.status not in (403, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524, 0):
                raise err
            if got_any:
                raise err

        # Fall back to non-streaming
        if last_err is not None:
            log.warning("Streaming failed with %s — falling back to non-streaming", last_err.status)
            try:
                non_stream = {k: v for k, v in payload.items() if k != "stream"}
                result = await self._post_with_retry(non_stream)
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

    async def vision(self, image_b64: str, prompt: str, mime: str = "image/png") -> str:
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
            result = await self.chat(messages, temperature=0.3, max_tokens=1024)
            return result["choices"][0]["message"]["content"]
        except KimiAPIError as e:
            return f"[Vision error {e.status}] {e.body[:200]}"
        except Exception as e:
            return f"[Vision error] {type(e).__name__}: {e}"

    async def ping(self) -> Dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 8,
            "temperature": 0.0,
        }
        try:
            result = await self._post_with_retry(payload)
            return {
                "ok": True,
                "model": self.model,
                "base_url": self.base_url,
                "reply": (result.get("choices") or [{}])[0].get("message", {}).get("content", ""),
            }
        except KimiAPIError as e:
            return {"ok": False, "status": e.status, "body": e.body[:400]}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
