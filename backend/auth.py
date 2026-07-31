"""
Authentication module — simple username/password auth with bcrypt hashing.

Owner account:
  * Username is fixed to `ADMIN` (case-sensitive on display; case-insensitive on login).
  * Password is taken from the `OWNER_PASS` secret at server startup.
    If the secret is missing we fall back to a random one-shot password and log a
    warning — this prevents accidentally shipping a well-known default.
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

OWNER_USERNAME = "ADMIN"


def _resolve_owner_password() -> str:
    """Read the owner password from the OWNER_PASS secret at startup."""
    pw = (os.getenv("OWNER_PASS") or "").strip()
    if pw:
        return pw
    # Fallback: random one-shot password so the owner account can't be logged into
    # with a well-known default when the secret is missing.
    fallback = secrets.token_urlsafe(18)
    log.warning(
        "OWNER_PASS secret is not set — generated a random ephemeral owner password. "
        "Set the OWNER_PASS repo secret to control the ADMIN password."
    )
    return fallback


OWNER_PASSWORD = _resolve_owner_password()


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


def _is_owner_name(name: str) -> bool:
    return (name or "").strip().lower() == OWNER_USERNAME.lower()


def ensure_owner():
    """
    Make sure exactly ONE owner account exists, its username is ADMIN, and its
    password matches the current OWNER_PASS secret.

    * Migrates any legacy 'admin' record → 'ADMIN'.
    * Refreshes the password hash whenever OWNER_PASS changes.
    """
    users = _load_users()
    changed = False

    # Migrate legacy lowercase 'admin' record
    legacy_key = None
    for k in list(users.keys()):
        if k != OWNER_USERNAME and k.lower() == OWNER_USERNAME.lower():
            legacy_key = k
            break
    if legacy_key:
        legacy = users.pop(legacy_key)
        legacy["username"] = OWNER_USERNAME
        legacy["is_owner"] = True
        users[OWNER_USERNAME] = legacy
        changed = True
        log.info("Migrated legacy owner account %r → %r", legacy_key, OWNER_USERNAME)

    pw_hash = bcrypt.hashpw(OWNER_PASSWORD.encode(), bcrypt.gensalt()).decode()

    if OWNER_USERNAME not in users:
        users[OWNER_USERNAME] = {
            "username": OWNER_USERNAME,
            "password_hash": pw_hash,
            "is_owner": True,
            "created_at": time.time(),
        }
        changed = True
        log.info("Created owner account: %s", OWNER_USERNAME)
    else:
        # Refresh password to match the current OWNER_PASS secret.
        stored = users[OWNER_USERNAME].get("password_hash", "").encode() or b""
        try:
            matches = bool(stored) and bcrypt.checkpw(OWNER_PASSWORD.encode(), stored)
        except Exception:
            matches = False
        if not matches:
            users[OWNER_USERNAME]["password_hash"] = pw_hash
            users[OWNER_USERNAME]["is_owner"] = True
            users[OWNER_USERNAME]["username"] = OWNER_USERNAME
            changed = True
            log.info("Refreshed owner password from OWNER_PASS secret")
        elif not users[OWNER_USERNAME].get("is_owner"):
            users[OWNER_USERNAME]["is_owner"] = True
            changed = True

    if changed:
        _save_users(users)


def register(username: str, password: str) -> Dict[str, Any]:
    """Create a new user. Returns {ok, error?}"""
    username = (username or "").strip()
    if not username or not password:
        return {"ok": False, "error": "Username and password are required"}
    if len(username) < 2 or len(username) > 40:
        return {"ok": False, "error": "Username must be 2-40 characters"}
    if len(password) < 3:
        return {"ok": False, "error": "Password must be at least 3 characters"}

    # Reserve the owner name — nobody else can register as ADMIN (any case).
    if _is_owner_name(username):
        return {"ok": False, "error": "This username is reserved"}

    users = _load_users()
    if username.lower() in {u.lower() for u in users.keys()}:
        return {"ok": False, "error": "Username already taken"}

    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    users[username] = {
        "username": username,
        "password_hash": pw_hash,
        "is_owner": False,
        "created_at": time.time(),
    }
    _save_users(users)
    return {"ok": True, "username": username, "is_owner": False}


def login(username: str, password: str) -> Dict[str, Any]:
    """Verify credentials and issue a session token."""
    username = (username or "").strip()
    if not username or password is None:
        return {"ok": False, "error": "Invalid username or password"}

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
    is_owner = bool(user.get("is_owner")) or _is_owner_name(username)
    # Normalise the displayed username to ADMIN if this is the owner
    if is_owner:
        username = OWNER_USERNAME
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
    if _is_owner_name(username):
        return {"ok": False, "error": "Cannot delete owner account"}
    users = _load_users()
    if username not in users:
        # case-insensitive fallback
        for k in list(users.keys()):
            if k.lower() == username.lower():
                username = k
                break
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
