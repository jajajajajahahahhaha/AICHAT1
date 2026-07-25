"""
Authentication module — simple username/password auth with bcrypt hashing.
Users are persisted to users.json (auto-committed to the repo via git in the
workflow, so accounts survive across Actions runs).
"""
import os
import json
import time
import secrets
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List

import bcrypt

log = logging.getLogger("auth")

ROOT = Path(__file__).resolve().parent.parent
USERS_FILE = ROOT / "data" / "users.json"
USERS_FILE.parent.mkdir(parents=True, exist_ok=True)

# in-memory session store: {token: {"username": str, "expires": float, "is_owner": bool}}
SESSIONS: Dict[str, Dict[str, Any]] = {}
SESSION_TTL = 7 * 24 * 3600  # 7 days

OWNER_USERNAME = "admin"
OWNER_PASSWORD = "admin"


def _load_users() -> Dict[str, Dict[str, Any]]:
    if not USERS_FILE.exists():
        return {}
    try:
        return json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("Failed to load users.json: %s", e)
        return {}


def _save_users(users: Dict[str, Dict[str, Any]]) -> None:
    USERS_FILE.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_owner():
    """Ensure the admin owner account exists."""
    users = _load_users()
    if OWNER_USERNAME not in users:
        pw_hash = bcrypt.hashpw(OWNER_PASSWORD.encode(), bcrypt.gensalt()).decode()
        users[OWNER_USERNAME] = {
            "username": OWNER_USERNAME,
            "password_hash": pw_hash,
            "is_owner": True,
            "created_at": time.time(),
        }
        _save_users(users)
        log.info("Created default owner account: %s", OWNER_USERNAME)


def register(username: str, password: str) -> Dict[str, Any]:
    """Create a new user. Returns {ok, error?}"""
    username = username.strip()
    if not username or not password:
        return {"ok": False, "error": "Username and password are required"}
    if len(username) < 2 or len(username) > 40:
        return {"ok": False, "error": "Username must be 2-40 characters"}
    if len(password) < 3:
        return {"ok": False, "error": "Password must be at least 3 characters"}

    users = _load_users()
    if username.lower() in {u.lower() for u in users.keys()}:
        return {"ok": False, "error": "Username already taken"}

    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    is_owner = (username == OWNER_USERNAME)
    users[username] = {
        "username": username,
        "password_hash": pw_hash,
        "is_owner": is_owner,
        "created_at": time.time(),
    }
    _save_users(users)
    return {"ok": True, "username": username, "is_owner": is_owner}


def login(username: str, password: str) -> Dict[str, Any]:
    """Verify credentials and issue a session token."""
    users = _load_users()
    user = users.get(username)
    # case-insensitive lookup fallback
    if not user:
        for k, v in users.items():
            if k.lower() == username.lower():
                user = v
                username = k
                break
    if not user:
        return {"ok": False, "error": "Invalid username or password"}
    try:
        if not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
            return {"ok": False, "error": "Invalid username or password"}
    except Exception:
        return {"ok": False, "error": "Invalid username or password"}

    token = secrets.token_urlsafe(32)
    is_owner = bool(user.get("is_owner")) or username == OWNER_USERNAME
    SESSIONS[token] = {
        "username": username,
        "is_owner": is_owner,
        "expires": time.time() + SESSION_TTL,
    }
    return {"ok": True, "token": token, "username": username, "is_owner": is_owner}


def verify_token(token: Optional[str]) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    session = SESSIONS.get(token)
    if not session:
        return None
    if session["expires"] < time.time():
        SESSIONS.pop(token, None)
        return None
    return session


def logout(token: Optional[str]) -> None:
    if token:
        SESSIONS.pop(token, None)


def list_users() -> List[Dict[str, Any]]:
    users = _load_users()
    return [
        {
            "username": u["username"],
            "is_owner": bool(u.get("is_owner")),
            "created_at": u.get("created_at", 0),
        }
        for u in users.values()
    ]


def delete_user(username: str) -> Dict[str, Any]:
    if username == OWNER_USERNAME:
        return {"ok": False, "error": "Cannot delete owner account"}
    users = _load_users()
    if username not in users:
        return {"ok": False, "error": "User not found"}
    del users[username]
    _save_users(users)
    # invalidate their sessions
    for tok in list(SESSIONS.keys()):
        if SESSIONS[tok]["username"] == username:
            SESSIONS.pop(tok, None)
    return {"ok": True}


# Initialize the owner on import
ensure_owner()
