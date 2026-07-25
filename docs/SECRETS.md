# GitHub Actions Secrets

The workflow requires these secrets to be configured in your repo:

**Settings → Secrets and variables → Actions → New repository secret**

| Name            | Purpose                                                                                | Example                                                    |
|-----------------|----------------------------------------------------------------------------------------|------------------------------------------------------------|
| `KIMI_API_KEY`  | API key for the inference service                                                      | `dahl_A5HQGAsRU579bpE6k89BSRitHGESaC6jc`                   |
| `KIMI_BASE_URL` | Cloudflare Worker proxy URL + `/v1` — the frontend hits this, which forwards upstream  | `https://kimi-proxy.abol89898.workers.dev/v1`              |
| `KIMI_MODEL`    | Default model id                                                                       | `moonshotai/Kimi-K2.6`                                     |
| `GH_TOKEN`      | Personal access token with `repo` scope (needed for auto-commit of chats + users.json) | `ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`                 |
| `GH_USERNAME`   | Your GitHub username                                                                   | `tydgggggg`                                                |

## Optional

| Name              | Purpose                                                              | Example        |
|-------------------|----------------------------------------------------------------------|----------------|
| `KIMI_PROXY`      | Extra HTTP proxy escape hatch (if the Cloudflare Worker is unavailable) | `http://...`  |
| `KIMI_IMPERSONATE`| Override curl_cffi Chrome impersonation profile                       | `chrome124`    |

## Why 5 secrets?

The user asked that the API key, base URL, and model name be secrets (**never committed**).
The GH token is required for the auto-commit persistence of chats/users across Actions runs.
The GH username is used in the "Create GitHub repo" feature (removed in v2.0 but env kept for compatibility).
