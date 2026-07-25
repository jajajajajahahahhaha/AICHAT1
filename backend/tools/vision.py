"""Image understanding tool - uses Kimi K2.6 with image input."""
from typing import Dict, Any


# In-memory store of uploaded images (session-scoped)
_IMAGE_STORE: Dict[str, Dict[str, str]] = {}


def store_image(image_id: str, b64: str, mime: str = "image/png") -> None:
    _IMAGE_STORE[image_id] = {"b64": b64, "mime": mime}


def get_image(image_id: str):
    return _IMAGE_STORE.get(image_id)


async def analyze_image(image_id: str, question: str) -> Dict[str, Any]:
    """Analyze a previously uploaded image with Kimi Vision."""
    from ..kimi_client import KimiClient  # lazy import to avoid cycles

    img = _IMAGE_STORE.get(image_id)
    if not img:
        return {"success": False, "error": f"Image {image_id} not found. Please upload it first."}

    client = KimiClient()
    try:
        answer = await client.vision(img["b64"], question, mime=img.get("mime", "image/png"))
        return {"success": True, "image_id": image_id, "analysis": answer}
    except Exception as e:
        return {"success": False, "error": f"{type(e).__name__}: {e}"}
