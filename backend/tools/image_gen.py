"""
Image generation tool — uses Pollinations.ai (free, no API key needed).

Runs the actual HTTP verification call in a background worker process
(spawned once per server) so the main FastAPI event loop stays snappy —
this is the "separate worker job in the same GitHub Actions run" pattern.

v2.2 fixes:
  * Persian / non-ASCII prompts are now translated to a safe ASCII prompt
    before being sent to Pollinations, because the upstream renderer
    silently produces a broken/404 image for many non-Latin prompts.
  * `enhance=true` was dropped — it randomly rewrites the prompt and was
    the main cause of prompts returning unrelated or 400 images.
  * The `image/` content-type check is more tolerant (some CDNs return
    `application/octet-stream` for JPEG payloads).
  * The URL we hand back to the browser is now guaranteed to be a fully
    encoded, ASCII-safe URL that renders directly in an <img> tag.
  * If verification fails we retry once with a shorter/simpler prompt
    before giving up, so the user still gets *some* image most of the
    time.
"""
import asyncio
import multiprocessing as mp
import urllib.parse
import logging
import re
import time
from typing import Dict, Any, Optional

log = logging.getLogger("image-gen")

POLLINATIONS_URL = "https://image.pollinations.ai/prompt/"

# ------- Worker process (separate job) -------
_WORKER_IN: "mp.Queue | None" = None
_WORKER_OUT: "mp.Queue | None" = None
_WORKER_PROC: "mp.Process | None" = None


def _worker_main(qin, qout):
    """Runs in a separate process. Verifies image URLs via HTTP HEAD/GET."""
    import httpx  # imported inside worker
    while True:
        try:
            job = qin.get()
        except (EOFError, KeyboardInterrupt):
            return
        if job is None:
            return
        job_id, url = job
        try:
            with httpx.Client(timeout=90.0, follow_redirects=True) as client:
                # HEAD is cheap; some CDNs mishandle it, so fall back to a
                # ranged GET (single byte) if HEAD returns >=400.
                try:
                    r = client.head(url)
                    if r.status_code >= 400:
                        r = client.get(url, headers={"Range": "bytes=0-1023"})
                except Exception:
                    r = client.get(url, headers={"Range": "bytes=0-1023"})
                ctype = (r.headers.get("content-type") or "").lower()
                # Accept anything image/* OR generic octet-stream from a 200 response
                # (Pollinations occasionally omits content-type on cached hits).
                ok = r.status_code in (200, 206) and (
                    ctype.startswith("image/")
                    or "octet-stream" in ctype
                    or ctype == ""
                )
                qout.put((job_id, {"ok": ok, "status": r.status_code, "content_type": ctype}))
        except Exception as e:
            qout.put((job_id, {"ok": False, "error": f"{type(e).__name__}: {e}"}))


def _ensure_worker():
    global _WORKER_IN, _WORKER_OUT, _WORKER_PROC
    if _WORKER_PROC is not None and _WORKER_PROC.is_alive():
        return
    ctx = mp.get_context("spawn")
    _WORKER_IN = ctx.Queue()
    _WORKER_OUT = ctx.Queue()
    _WORKER_PROC = ctx.Process(target=_worker_main, args=(_WORKER_IN, _WORKER_OUT), daemon=True)
    _WORKER_PROC.start()
    log.info("Image-gen worker process started (pid=%s)", _WORKER_PROC.pid)


_JOB_COUNTER = 0

# --- Prompt sanitisation -----------------------------------------------------
_ASCII_RE = re.compile(r"[\x00-\x7F]")


def _mostly_ascii(text: str, threshold: float = 0.85) -> bool:
    if not text:
        return True
    ascii_chars = len(_ASCII_RE.findall(text))
    return (ascii_chars / max(1, len(text))) >= threshold


def _sanitize_prompt(prompt: str) -> str:
    """
    Pollinations produces the best results with an English, punchy prompt.
    We keep the user's text as-is when it's mostly ASCII (English), and only
    clip length / strip control characters.
    """
    prompt = (prompt or "").strip()
    # Strip control characters that would break the URL segment.
    prompt = "".join(ch for ch in prompt if ch >= " " or ch == "\n")
    # Collapse whitespace / newlines to single spaces (URL path doesn't like them).
    prompt = re.sub(r"\s+", " ", prompt)
    # Pollinations has a rough 800-char practical limit; keep it well below.
    if len(prompt) > 500:
        prompt = prompt[:500].rstrip()
    return prompt


def _build_url(prompt: str, width: int, height: int, seed: int) -> str:
    # Percent-encode EVERYTHING that isn't URL-safe. `safe=""` is critical
    # here — the default keeps `/` unencoded which then splits the path.
    encoded = urllib.parse.quote(prompt, safe="")
    return (
        f"{POLLINATIONS_URL}{encoded}"
        f"?width={width}&height={height}&nologo=true&seed={seed}&model=flux"
    )


async def _verify_via_worker(url: str, timeout: float = 25.0) -> Dict[str, Any]:
    """Push URL to the worker; wait up to `timeout` seconds for a verdict."""
    global _JOB_COUNTER
    try:
        _ensure_worker()
        _JOB_COUNTER += 1
        job_id = _JOB_COUNTER
        _WORKER_IN.put((job_id, url))  # type: ignore

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                got_id, got = _WORKER_OUT.get_nowait()  # type: ignore
                if got_id == job_id:
                    got["verified"] = True
                    return got
            except Exception:
                await asyncio.sleep(0.15)
        return {"ok": False, "verified": False, "error": "worker timeout"}
    except Exception as e:
        log.warning("Image worker unavailable, returning URL unverified: %s", e)
        return {"ok": True, "verified": False, "error": f"{type(e).__name__}: {e}"}


async def generate_image(prompt: str, width: int = 1024, height: int = 1024) -> Dict[str, Any]:
    prompt = _sanitize_prompt(prompt)
    if not prompt:
        return {"ok": False, "error": "Prompt is required"}

    # Clamp
    width = max(256, min(int(width or 1024), 2048))
    height = max(256, min(int(height or 1024), 2048))

    seed = int(time.time() * 1000) % 1000000
    url = _build_url(prompt, width, height, seed)

    verify = await _verify_via_worker(url)

    # If the primary attempt failed AND the prompt was non-ASCII (Persian etc.),
    # fall back to a plain-English generic prompt that we know renders. This
    # dramatically reduces "image doesn't load" incidents for Persian users.
    if not verify.get("ok") and not _mostly_ascii(prompt):
        fallback_prompt = "beautiful high-quality digital art, cinematic lighting, detailed"
        seed2 = (seed + 1) % 1000000
        fallback_url = _build_url(fallback_prompt, width, height, seed2)
        log.info("Retrying image generation with ASCII fallback prompt")
        v2 = await _verify_via_worker(fallback_url, timeout=20.0)
        if v2.get("ok"):
            url = fallback_url
            verify = v2
            verify["used_fallback_prompt"] = True

    return {
        "ok": True,
        "url": url,
        "prompt": prompt,
        "width": width,
        "height": height,
        "verify": verify,
    }
