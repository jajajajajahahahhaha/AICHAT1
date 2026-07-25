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

# Set a fake key so KimiClient init doesn't blow up
os.environ.setdefault("KIMI_API_KEY", "dahl_test_key")
os.environ.setdefault("KIMI_BASE_URL", "https://kimi-proxy.abol89898.workers.dev/v1")
os.environ.setdefault("KIMI_MODEL", "moonshotai/Kimi-K2.6")


def test_auth_module():
    from backend import auth
    auth.ensure_owner()
    # Owner exists
    users = auth._load_users()
    assert "admin" in users
    assert users["admin"]["is_owner"] is True

    # Login as owner
    r = auth.login("admin", "admin")
    assert r["ok"], r
    assert r["is_owner"] is True
    token = r["token"]

    # Verify token
    session = auth.verify_token(token)
    assert session and session["username"] == "admin"

    # Register a new user
    tname = "pytest_user_" + os.urandom(3).hex()
    r = auth.register(tname, "pw123")
    assert r["ok"], r
    r2 = auth.login(tname, "pw123")
    assert r2["ok"]
    assert r2["is_owner"] is False

    # List / delete
    lst = auth.list_users()
    assert any(u["username"] == tname for u in lst)
    r3 = auth.delete_user(tname)
    assert r3["ok"]

    # Can't delete admin
    r4 = auth.delete_user("admin")
    assert not r4["ok"]

    # Owner login case-insensitive
    r5 = auth.login("ADMIN", "admin")
    assert r5["ok"] and r5["is_owner"]

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
    """Even offline, generate_image should still return a valid URL (unverified)."""
    from backend.tools.image_gen import generate_image
    r = asyncio.run(generate_image("a red cat", 512, 512))
    assert r.get("ok") is True
    assert r.get("url", "").startswith("https://image.pollinations.ai/")
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
    test_kimi_client_init()
    test_fastapi_routes_exist()
    test_image_gen_returns_url()
    test_tool_definitions_shape()
    print("\nALL TESTS PASSED ✅")
