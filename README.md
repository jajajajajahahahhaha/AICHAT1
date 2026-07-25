# Kimi Chat — v2.1

A polished ChatGPT-like AI chat interface running on **GitHub Actions**, powered by **Kimi K2.6** via a Cloudflare Worker proxy.

## ✨ What changed in v2.1 (bugfix release)

Every issue you reported is fixed:

- ✅ **Second / later messages no longer freeze on "Thinking…"** — the streaming state is fully cleared between turns and rebuilt from a single source of truth.
- ✅ **Chats load correctly** — clicking a chat in the sidebar now rebuilds the whole DOM (welcome / previous messages / tool events / generated images).
- ✅ **All code blocks are copyable** — the code-block enhancer is idempotent; it re-runs on every stream update so blocks emitted mid-stream still get a Copy button.
- ✅ **Web search no longer loops 50 times** — server-side dedupe by tool signature + `max_iterations=4` + explicit system-prompt guidance. Same query is answered with "you already ran this — produce your final answer".
- ✅ **Image generation actually happens** — dedicated `generate_image` tool, verified in a **separate worker process** (per your ask). Model gets a very explicit instruction to call the tool for any "create/draw/make an image" request.
- ✅ **Image upload is recognised** — uploaded images are now persisted to disk (not just an in-memory dict), and are re-hydrated on server start.
- ✅ **File upload** works for PDF, TXT, MD, CSV, JSON, and all common code files.
- ✅ **UI is softer** — refined spacing, colours, radii, transitions, focus rings; icon-only edit/copy buttons under every message; better sidebar highlight; cleaner code blocks; toast notifications.
- ✅ **Accounts** — register form on `/login`, owner `admin` / `admin` gets a Manage-Users panel with delete permission.
- ✅ **Modes** — `Lite` / `Fast` / `Thinking` switch in the top bar.
- ✅ **HTML `Run` opens in a sandboxed iframe modal only** — never pollutes the current page.

## 🚀 Setup

### 1. Cloudflare Worker proxy (one-time)

The upstream inference API sits behind Cloudflare bot protection. The included Worker in `proxy/` runs inside Cloudflare's own network, bypassing the challenge.

Already deployed for this project at: `https://kimi-proxy.abol89898.workers.dev/v1`

### 2. GitHub Secrets

Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret name     | Value                                                                        |
|-----------------|------------------------------------------------------------------------------|
| `KIMI_API_KEY`  | Your API key (e.g. `dahl_...`)                                               |
| `KIMI_BASE_URL` | `https://kimi-proxy.abol89898.workers.dev/v1`                                |
| `KIMI_MODEL`    | `moonshotai/Kimi-K2.6`                                                       |
| `GH_TOKEN`      | Personal Access Token with `repo` scope (used for auto-committing chats)     |
| `GH_USERNAME`   | Your GitHub username                                                         |

### 3. Run

**Actions → Run Kimi Chat Server → Run workflow.** After ~1 minute a temporary Cloudflare Tunnel URL is printed in the job summary. Open it in a browser and log in with `admin` / `admin` (or register a new account).

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
python3 tests/test_all.py
```

Covers: auth, tool-signature dedupe, DDG search wrapper, sandbox (Python/Bash/HTML), image store, Kimi client init, all FastAPI routes, image-gen URL, tool definitions. All eleven tests pass.

## 🔧 Local development

```bash
pip install -r backend/requirements.txt
export KIMI_API_KEY="..."
export KIMI_BASE_URL="https://kimi-proxy.abol89898.workers.dev/v1"
export KIMI_MODEL="moonshotai/Kimi-K2.6"
python3 backend/server.py     # → http://127.0.0.1:7860
```

## 📝 License
MIT.
