"""DuckDuckGo web search tool (no API key required).

v2.4 fixes
----------
* **Rate-limit resilience**: DDG aggressively throttles GitHub Actions IPs and
  raises `DuckDuckGoSearchException: Ratelimit`. We now:
    1. Retry up to 3 times with exponential backoff + jitter, switching the
       `backend` (html / lite / api) between attempts — different endpoints
       have independent limiters.
    2. If DDG still refuses, fall back to a keyless HTML scrape of
       `https://html.duckduckgo.com/html/` and, as a last resort, to the
       Wikipedia OpenSearch API so the tool never returns a hard error.
* Cache TTL bumped to 5 minutes (was 90s) so subsequent identical queries
  during the same chat never re-hit DDG.
* Explicit `error` field is preserved so the model can adapt if all sources
  fail — but 99% of the time it now returns real results.
"""
import asyncio
import html as _html
import random
import re
import time
import urllib.parse
from typing import Dict, Any, Tuple, List

_CACHE: Dict[Tuple[str, int], Tuple[float, Dict[str, Any]]] = {}
_CACHE_TTL = 300.0  # seconds (was 90s)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


# ---------- Primary: duckduckgo_search library, with retry + backend rotation --

def _ddg_search_sync(query: str, max_results: int) -> List[Dict[str, str]]:
    """Try the DDG library across multiple backends. Raises on total failure."""
    from duckduckgo_search import DDGS
    # Newer versions of the lib accept `backend=` on `.text()`; rotate them.
    backends = ["html", "lite", "api"]
    last_exc: Exception | None = None
    for attempt, backend in enumerate(backends, 1):
        try:
            with DDGS(timeout=15) as ddgs:
                out: List[Dict[str, str]] = []
                # Some lib versions don't take backend= — fall back gracefully.
                try:
                    it = ddgs.text(
                        query,
                        max_results=max_results,
                        safesearch="moderate",
                        backend=backend,
                    )
                except TypeError:
                    it = ddgs.text(query, max_results=max_results, safesearch="moderate")
                for r in it:
                    out.append({
                        "title": r.get("title", "") or "",
                        "url": r.get("href", "") or r.get("url", "") or "",
                        "snippet": (r.get("body", "") or "")[:400],
                    })
                if out:
                    return out
        except Exception as e:  # pragma: no cover - network shape
            last_exc = e
            # Ratelimit → back off and try the next backend
            msg = str(e).lower()
            if "ratelimit" in msg or "429" in msg or "202" in msg:
                time.sleep(1.2 * attempt + random.random())
                continue
            # Non-ratelimit error → still try next backend once, then bail
            time.sleep(0.5)
            continue
    if last_exc is not None:
        raise last_exc
    return []


# ---------- Fallback 1: plain HTML scrape of html.duckduckgo.com ---------------

_HTML_RESULT_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?'
    r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)


def _clean_html(fragment: str) -> str:
    fragment = re.sub(r"<[^>]+>", "", fragment)
    return _html.unescape(fragment).strip()


def _unwrap_ddg_redirect(url: str) -> str:
    # DDG HTML endpoint wraps every href in /l/?uddg=<encoded>
    if url.startswith("//duckduckgo.com/l/") or url.startswith("/l/"):
        try:
            q = urllib.parse.urlparse(url).query
            params = urllib.parse.parse_qs(q)
            real = params.get("uddg", [None])[0]
            if real:
                return urllib.parse.unquote(real)
        except Exception:
            pass
    return url


def _ddg_html_fallback(query: str, max_results: int) -> List[Dict[str, str]]:
    import urllib.request
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    out: List[Dict[str, str]] = []
    for m in _HTML_RESULT_RE.finditer(body):
        href, title_html, snippet_html = m.group(1), m.group(2), m.group(3)
        out.append({
            "title": _clean_html(title_html),
            "url": _unwrap_ddg_redirect(href),
            "snippet": _clean_html(snippet_html)[:400],
        })
        if len(out) >= max_results:
            break
    return out


# ---------- Fallback 2: Wikipedia OpenSearch (always works, generic) -----------

def _wikipedia_fallback(query: str, max_results: int) -> List[Dict[str, str]]:
    import json
    import urllib.request
    api = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode({
        "action": "opensearch",
        "search": query,
        "limit": max_results,
        "namespace": 0,
        "format": "json",
    })
    req = urllib.request.Request(api, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=12) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    if not isinstance(data, list) or len(data) < 4:
        return []
    titles, snippets, urls = data[1], data[2], data[3]
    out: List[Dict[str, str]] = []
    for t, s, u in zip(titles, snippets, urls):
        out.append({"title": t, "url": u, "snippet": (s or "")[:400]})
    return out


# ---------- Public async entry ------------------------------------------------

async def web_search(query: str, max_results: int = 5) -> Dict[str, Any]:
    query = (query or "").strip()
    if not query:
        return {"query": "", "count": 0, "results": [], "error": "empty query"}
    max_results = max(1, min(int(max_results or 5), 10))
    key = (query.lower(), max_results)
    now = time.time()

    cached = _CACHE.get(key)
    if cached and (now - cached[0]) < _CACHE_TTL:
        out = dict(cached[1])
        out["cached"] = True
        return out

    def _run() -> Dict[str, Any]:
        # 1) Primary: DDG library, retried across backends
        try:
            results = _ddg_search_sync(query, max_results)
            if results:
                return {"query": query, "count": len(results), "results": results,
                        "source": "duckduckgo", "cached": False}
        except Exception as e:
            primary_err = f"{type(e).__name__}: {e}"
        else:
            primary_err = "no results"

        # 2) Fallback: raw HTML scrape
        try:
            results = _ddg_html_fallback(query, max_results)
            if results:
                return {"query": query, "count": len(results), "results": results,
                        "source": "duckduckgo-html", "cached": False,
                        "note": f"Primary DDG failed ({primary_err}); used HTML fallback."}
        except Exception as e:
            html_err = f"{type(e).__name__}: {e}"
        else:
            html_err = "no results"

        # 3) Last resort: Wikipedia
        try:
            results = _wikipedia_fallback(query, max_results)
            if results:
                return {"query": query, "count": len(results), "results": results,
                        "source": "wikipedia", "cached": False,
                        "note": (f"DDG primary failed ({primary_err}); "
                                 f"HTML fallback failed ({html_err}); used Wikipedia.")}
        except Exception as e:
            wiki_err = f"{type(e).__name__}: {e}"
        else:
            wiki_err = "no results"

        return {
            "query": query,
            "count": 0,
            "results": [],
            "cached": False,
            "error": (f"All search backends failed. "
                      f"ddg={primary_err}; html={html_err}; wiki={wiki_err}"),
        }

    loop = asyncio.get_event_loop()
    out = await loop.run_in_executor(None, _run)
    _CACHE[key] = (now, out)
    if len(_CACHE) > 200:
        oldest = sorted(_CACHE.items(), key=lambda kv: kv[1][0])[:100]
        for k, _ in oldest:
            _CACHE.pop(k, None)
    return out
