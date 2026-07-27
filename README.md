# Kimi Chat — v2.2

A polished ChatGPT-like AI chat interface running on **GitHub Actions**, powered by **Kimi K2.6** via a Cloudflare Worker proxy.

## ✨ What's new in v2.2 (bug-fix + polish release)

Every issue reported in v2.1 is fixed:

- ✅ **"Some messages get no answer" bug** — the frontend now guarantees strict `user`/`assistant` alternation before hitting the model, and the backend falls back to a forced text reply if the model tries to end a turn with no output.
- ✅ **Broken image URL** — generation now percent-encodes the whole prompt (including Persian), drops the flaky `enhance=true` flag, retries on 404, and auto-falls back to a safe English prompt when a non-ASCII one won't render. The frontend also re-tries the `<img>` twice with a small back-off, and if it still fails shows a clickable "open directly" link instead of a broken-icon.
- ✅ **`analyze_image` tool actually works** — the system prompt now REQUIRES calling it whenever the user asks about an attached image, and the tool lazily rehydrates images from disk so a restart between upload and analyze doesn't kill it.
- ✅ **UI is noticeably softer** — new indigo/violet palette, gradient send button with shadow, gentler easing curves, subtle hover translations on chat items / cards, an animated placeholder box while an image loads.
- ✅ **Owner account hardened** — username is now `ADMIN` (fixed, reserved), password is read at startup from the `OWNER_PASS` secret, and the login page no longer advertises the credentials. Legacy `admin` records auto-migrate.

## 🚀 Setup

### 1. Cloudflare Worker proxy (one-time)

The upstream inference API sits behind Cloudflare bot protection. The included Worker in `proxy/` runs inside Cloudflare's own network, bypassing the challenge.

Already deployed at: `https://kimi-proxy.abol89898.workers.dev/v1`

### 2. GitHub Secrets

Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret name     | Value                                                                        |
|-----------------|------------------------------------------------------------------------------|
| `KIMI_API_KEY`  | Your API key (e.g. `dahl_...`)                                               |
| `KIMI_BASE_URL` | `https://kimi-proxy.abol89898.workers.dev/v1`                                |
| `KIMI_MODEL`    | `moonshotai/Kimi-K2.6`                                                       |
| `GH_TOKEN`      | Personal Access Token with `repo` scope (used for auto-committing chats)     |
| `GH_USERNAME`   | Your GitHub username                                                         |
| `OWNER_PASS`    | Password for the owner account (`ADMIN`). Keep this secret.                  |

### 3. Run

**Actions → Run Kimi Chat Server → Run workflow.** After ~1 minute a temporary Cloudflare Tunnel URL is printed in the job summary. Open it in a browser and either:

- **Register** a new account from the sign-up tab, or
- **Log in** as the owner with username `ADMIN` and the password you set in the `OWNER_PASS` secret (the credentials are never shown on the login page).

## 🧰 Tools the model can call

| Tool             | What it does                                                        |
|------------------|---------------------------------------------------------------------|
| `web_search`     | DuckDuckGo (no key needed). Result is cached in-process for 90s.    |
| `run_code`       | Python / Bash / HTML sandbox inside the Actions runner.             |
| `analyze_image`  | Kimi K2.6 vision on an uploaded image (image_id).                   |
| `generate_image` | Pollinations.ai in a separate worker process. Returns an image URL. |

## 🏗️ Architecture

```
Frontend (static HTML/CSS/JS)
      ↓
FastAPI backend (backend/server.py)
      ├── Auth (bcrypt + in-memory sessions, users.json persisted to repo)
      ├── Tools: search / sandbox / vision / image_gen (worker process)
      └── Streaming chat proxy → Cloudflare Worker → Upstream Inference API
```

- Chats: `data/chats/<username>/<chat_id>.json` + browser localStorage
- Users: `data/users.json` (bcrypt-hashed)
- Both auto-committed back to the repo every ~10 minutes so they survive Actions restarts.

## 🧪 Tests

```bash
OWNER_PASS=test_owner_pw_123 python3 tests/test_all.py
```

Covers: auth (incl. reserved-name + case-insensitive login), tool-signature dedupe, DDG search wrapper, sandbox (Python/Bash/HTML), image store, `analyze_image` missing-id, Kimi client init, all FastAPI routes, image-gen URL (ASCII-safe for Persian prompts too), tool definitions. All twelve tests pass.

## 🔧 Local development

```bash
pip install -r backend/requirements.txt
export KIMI_API_KEY="..."
export KIMI_BASE_URL="https://kimi-proxy.abol89898.workers.dev/v1"
export KIMI_MODEL="moonshotai/Kimi-K2.6"
export OWNER_PASS="pick-something-strong"
python3 backend/server.py     # → http://127.0.0.1:7860
```

## 📝 License
MIT.
