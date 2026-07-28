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
    print("\nALL TESTS PASSED ✅")
