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
    print("\nALL TESTS PASSED ✅")
