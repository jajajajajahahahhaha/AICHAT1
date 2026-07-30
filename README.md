# Kimi & MiniMax Chat — v3.0

A polished ChatGPT-like AI chat interface running on **GitHub Actions**, powered by **Kimi K2.6** and **MiniMax M2.7** via a Cloudflare Worker proxy.

## 🚀 What's new in v3.0 — Sandbox v3

The biggest upgrade since launch. The old sandbox was a tiny 30s python/bash/html
runner — the new one is a genuine multi-language development environment:

- ✅ **17 languages** — python, bash, html, **javascript (node)**, **typescript**,
  **c**, **cpp**, **go**, **rust**, **java**, **kotlin**, **ruby**, **php**,
  **lua**, **r**, **sql (sqlite)**, **perl** — plus `language="auto"` to
  detect the language from a shebang.
- ✅ **Bigger limits** — default timeout 90s (was 30s), max 300s. stdout/stderr
  cap raised from 10 KB to 200 KB. Address-space limit configurable (default 2 GB).
- ✅ **Persistent workspaces** — pass `workspace_id` to keep files across
  multiple `run_code` calls in the same chat. Build a mini-project step by step.
- ✅ **Auto-install packages** — pass `packages=["requests","pandas"]` and the
  sandbox pip/npm/gem-installs them before running your code.
- ✅ **stdin / env / seed files** — pipe input, inject env vars, or drop extra
  files into the workspace (`go.mod`, `package.json`, seed CSVs…).
- ✅ **Artifacts** — files your code produces (plots, CSVs, binaries) come back
  as downloadable URLs; small PNGs are also embedded inline as data URLs.
  `matplotlib.pyplot.show()` auto-saves to `plot.png`.
- ✅ **Compile vs. runtime errors** — for compiled languages the compile step
  is reported separately (`compile.stderr`), so debugging is precise.
- ✅ **Resource limits** — Linux `RLIMIT_CPU` / `RLIMIT_AS` clamp runaway code.
- ✅ **Path-traversal-safe** — workspaces are per-id sandboxes; the file
  download endpoint refuses `../` escapes.
- ✅ **Full backward compatibility** — the old two-arg `run_code(language, code)`
  and its result shape (`success/stdout/stderr/returncode/language`) still work.

New endpoints:

| Endpoint                                                | What it does                                        |
|---------------------------------------------------------|-----------------------------------------------------|
| `GET  /api/sandbox/languages`                           | Returns the list of executable languages            |
| `GET  /api/sandbox/files/{workspace_id}`                | List files inside a workspace                       |
| `GET  /api/sandbox/files/{workspace_id}/{path}`         | Download a single artifact (inline)                 |
| `POST /api/sandbox/sweep` (owner only)                  | Purge ephemeral workspaces older than 1 h           |

## ✨ What was new in v2.3

- ✅ **Model switcher (Kimi ↔ MiniMax)** — Both `moonshotai/Kimi-K2.6` and `MiniMaxAI/MiniMax-M2.7` are now first-class citizens. The frontend model picker lists both; per-request routing sends traffic to the right provider automatically.
- ✅ **`429 rate limit exceeded: too many concurrent requests` fixed** — The client now retries `429` (and 5xx) with **exponential backoff + jitter**, and honours `Retry-After` when the upstream sends it. Applies to both streaming and non-streaming paths.
- ✅ **Image analysis fixed** — `analyze_image` now uses the user's *currently selected* model. If that model turns out not to be vision-capable, we auto-fall-back to a vision model instead of failing silently. Errors from the upstream vision endpoint are surfaced verbatim (status + body) so failures are debuggable.

Everything from v2.2 (strict user/assistant alternation, image URL percent-encoding, softer indigo/violet UI, hardened owner login) is preserved.

## 🚀 Setup

### 1. Cloudflare Worker proxy (one-time)

The upstream inference API sits behind Cloudflare bot protection. The included Worker in `proxy/` runs inside Cloudflare's own network, bypassing the challenge.

Already deployed at: `https://kimi-proxy.abol89898.workers.dev/v1`

### 2. GitHub Secrets

Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret name        | Value                                                                        |
|--------------------|------------------------------------------------------------------------------|
| `KIMI_API_KEY`     | Your API key (works for both Kimi and MiniMax on the same proxy)             |
| `KIMI_BASE_URL`    | `https://kimi-proxy.abol89898.workers.dev/v1`                                |
| `KIMI_MODEL`       | Default model. `moonshotai/Kimi-K2.6` **or** `MiniMaxAI/MiniMax-M2.7`        |
| `MINIMAX_API_KEY`  | *(optional)* Separate key for MiniMax if it lives outside the shared proxy   |
| `MINIMAX_BASE_URL` | *(optional)* Separate base URL for MiniMax (defaults to `KIMI_BASE_URL`)     |
| `GH_TOKEN`         | Personal Access Token with `repo` scope (used for auto-committing chats)     |
| `GH_USERNAME`      | Your GitHub username                                                         |
| `OWNER_PASS`       | Password for the owner account (`ADMIN`). Keep this secret.                  |

> **Tip:** if both models live on the same Cloudflare Worker proxy (the default), you only need `KIMI_API_KEY` — the client uses it for both providers.

### 3. Run

**Actions → Run Kimi Chat Server → Run workflow.** After ~1 minute a temporary Cloudflare Tunnel URL is printed in the job summary. Open it in a browser and either:

- **Register** a new account from the sign-up tab, or
- **Log in** as the owner with username `ADMIN` and the password you set in the `OWNER_PASS` secret (the credentials are never shown on the login page).

Use the **model selector at the top of the chat view** to switch between Kimi K2.6 and MiniMax M2.7 at any time — every subsequent message goes to the chosen model.

## 🧰 Tools the model can call

| Tool             | What it does                                                        |
|------------------|---------------------------------------------------------------------|
| `web_search`     | DuckDuckGo (no key needed). Result is cached in-process for 90s.    |
| `run_code`       | Python / Bash / HTML sandbox inside the Actions runner.             |
| `analyze_image`  | Vision on an uploaded image, using the user's active model.         |
| `generate_image` | Pollinations.ai in a separate worker process. Returns an image URL. |

## 🏗️ Architecture

```
Frontend (static HTML/CSS/JS)
      ↓
FastAPI backend (backend/server.py)
      ├── Auth (bcrypt + in-memory sessions, users.json persisted to repo)
      ├── Tools: search / sandbox / vision / image_gen (worker process)
      └── Streaming chat proxy → Cloudflare Worker → {Kimi K2.6 | MiniMax M2.7}
              (with 429/5xx retry + exponential backoff)
```

- Chats: `data/chats/<username>/<chat_id>.json` + browser localStorage
- Users: `data/users.json` (bcrypt-hashed)
- Both auto-committed back to the repo every ~10 minutes so they survive Actions restarts.

## 🧪 Tests

```bash
OWNER_PASS=test_owner_pw_123 python3 tests/test_all.py
```

Covers: auth (incl. reserved-name + case-insensitive login), tool-signature dedupe, DDG search wrapper, sandbox (Python/Bash/HTML), image store, `analyze_image` missing-id, Kimi client init, all FastAPI routes, image-gen URL (ASCII-safe for Persian prompts too), tool definitions, **multi-model catalogue (Kimi + MiniMax)**, **429 backoff helper**. All tests pass.

## 🔧 Local development

```bash
pip install -r backend/requirements.txt
export KIMI_API_KEY="..."
export KIMI_BASE_URL="https://kimi-proxy.abol89898.workers.dev/v1"
export KIMI_MODEL="moonshotai/Kimi-K2.6"    # or MiniMaxAI/MiniMax-M2.7
export OWNER_PASS="pick-something-strong"
python3 backend/server.py     # → http://127.0.0.1:7860
```

## 📝 License
MIT.
