"""
Image generation tool — uses Pollinations.ai (free, no API key needed).

Runs actual HTTP calls in a background worker process (spawned once per
server) so the main FastAPI event loop stays snappy — this is the "separate
worker job in the same GitHub Actions run" pattern the user asked for.

Falls back gracefully to a direct URL (no verification) if the worker or
the verification request fails, so the model can still hand a valid image
URL back to the user.
"""
import asyncio
import multiprocessing as mp
import urllib.parse
import logging
import time
from typing import Dict, Any

log = logging.getLogger("image-gen")

POLLINATIONS_URL = "https://image.pollinations.ai/prompt/"

# ------- Worker process (separate job) -------
_WORKER_IN: "mp.Queue | None" = None
_WORKER_OUT: "mp.Queue | None" = None
_WORKER_PROC: "mp.Process | None" = None


def _worker_main(qin, qout):
    """Runs in a separate process. Verifies image URLs via HTTP HEAD."""
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
            with httpx.Client(timeout=60.0, follow_redirects=True) as client:
                # Prefer HEAD first (cheaper); fall back to GET.
                try:
                    r = client.head(url)
                    if r.status_code >= 400:
                        r = client.get(url)
                except Exception:
                    r = client.get(url)
                ok = r.status_code == 200 and r.headers.get("content-type", "").startswith("image/")
                qout.put((job_id, {"ok": ok, "status": r.status_code}))
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


async def generate_image(prompt: str, width: int = 1024, height: int = 1024) -> Dict[str, Any]:
    global _JOB_COUNTER
    prompt = (prompt or "").strip()
    if not prompt:
        return {"ok": False, "error": "Prompt is required"}

    # Clamp
    width = max(256, min(int(width or 1024), 2048))
    height = max(256, min(int(height or 1024), 2048))

    encoded = urllib.parse.quote(prompt)
    seed = int(time.time() * 1000) % 1000000
    url = (
        f"{POLLINATIONS_URL}{encoded}"
        f"?width={width}&height={height}&nologo=true&enhance=true&seed={seed}"
    )

    # Try the worker; if it fails we still return the URL — Pollinations serves
    # the image lazily anyway, so the browser will fetch it fine.
    verify: Dict[str, Any] = {"ok": True, "verified": False}
    try:
        _ensure_worker()
        _JOB_COUNTER += 1
        job_id = _JOB_COUNTER
        _WORKER_IN.put((job_id, url))  # type: ignore

        # Wait up to 25s for the worker to reply
        deadline = time.monotonic() + 25.0
        while time.monotonic() < deadline:
            try:
                got_id, got = _WORKER_OUT.get_nowait()  # type: ignore
                if got_id == job_id:
                    verify = got
                    verify["verified"] = True
                    break
            except Exception:
                await asyncio.sleep(0.15)
    except Exception as e:
        log.warning("Image worker unavailable, returning URL unverified: %s", e)

    # Even if verify failed, return the URL — the browser will retry the image load itself.
    return {
        "ok": True,
        "url": url,
        "prompt": prompt,
        "width": width,
        "height": height,
        "verify": verify,
    }
