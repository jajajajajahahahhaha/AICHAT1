"""
Image understanding tool - uses Kimi K2.6 with image input.

v2.2 fixes:
  * If the model calls analyze_image with a bad / stale image_id, we now
    try to locate the image on disk (rehydrate lazily) BEFORE giving up,
    so a restart between upload and analyze doesn't break the tool.
  * The tool no longer instantiates a brand-new KimiClient on every call
    (the API key is read once).
  * A clearer, structured result payload — {success, analysis, image_id,
    error} — so the model can react to a failure properly instead of
    swallowing an opaque error string.
  * If the vision call itself errors we surface the actual message so
    the user sees WHY (e.g. quota / auth), rather than a silent fail.
"""
import base64
import logging
from pathlib import Path
from typing import Dict, Any, Optional

log = logging.getLogger("vision")

ROOT = Path(__file__).resolve().parent.parent.parent
IMAGES_DIR = ROOT / "data" / "images"

# In-memory store of uploaded images (session-scoped)
_IMAGE_STORE: Dict[str, Dict[str, str]] = {}


def store_image(image_id: str, b64: str, mime: str = "image/png") -> None:
    _IMAGE_STORE[image_id] = {"b64": b64, "mime": mime}


def get_image(image_id: str) -> Optional[Dict[str, str]]:
    img = _IMAGE_STORE.get(image_id)
    if img:
        return img
    # Lazy rehydrate from disk (survives worker restarts / cold cache)
    return _lazy_load_from_disk(image_id)


def _lazy_load_from_disk(image_id: str) -> Optional[Dict[str, str]]:
    if not image_id or not image_id.startswith("img_"):
        return None
    meta = IMAGES_DIR / f"{image_id}.meta"
    if not meta.exists():
        return None
    try:
        mime = meta.read_text(encoding="utf-8").strip() or "image/png"
        for cand in IMAGES_DIR.glob(f"{image_id}.*"):
            if cand.suffix == ".meta":
                continue
            b64 = base64.b64encode(cand.read_bytes()).decode("ascii")
            store_image(image_id, b64, mime)
            log.info("Lazily rehydrated image %s from disk", image_id)
            return _IMAGE_STORE[image_id]
    except Exception as e:
        log.warning("Failed lazy rehydrate of %s: %s", image_id, e)
    return None


async def analyze_image(image_id: str, question: str) -> Dict[str, Any]:
    """Analyze a previously uploaded image with Kimi Vision."""
    from ..kimi_client import KimiClient, KimiAPIError  # lazy import

    image_id = (image_id or "").strip()
    question = (question or "").strip() or "Describe this image in detail."

    img = get_image(image_id)
    if not img:
        return {
            "success": False,
            "image_id": image_id,
            "error": (
                f"Image {image_id!r} not found. "
                "The user must upload it via the paperclip button first, "
                "and you must pass the exact image_id (starts with 'img_') "
                "you were told about."
            ),
        }

    try:
        client = KimiClient()
    except Exception as e:
        return {
            "success": False,
            "image_id": image_id,
            "error": f"Vision client not configured: {type(e).__name__}: {e}",
        }

    try:
        answer = await client.vision(
            img["b64"], question, mime=img.get("mime", "image/png")
        )
        # `vision()` catches its own errors and returns them as a string that
        # starts with '[Vision error'. Detect that and route through error field.
        if isinstance(answer, str) and answer.startswith("[Vision error"):
            return {
                "success": False,
                "image_id": image_id,
                "error": answer,
            }
        return {
            "success": True,
            "image_id": image_id,
            "question": question,
            "analysis": answer,
        }
    except KimiAPIError as e:
        return {
            "success": False,
            "image_id": image_id,
            "error": f"Kimi API error {e.status}: {e.body[:300]}",
        }
    except Exception as e:
        log.exception("analyze_image failed")
        return {
            "success": False,
            "image_id": image_id,
            "error": f"{type(e).__name__}: {e}",
        }
