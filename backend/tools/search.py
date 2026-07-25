"""DuckDuckGo web search tool (no API key required).

Includes an in-process cache so the model doesn't hammer DDG when it decides
to re-search the same query (previous bug: model looped and issued 20+ identical
searches). Cache TTL is short so we don't lock in stale results.
"""
import asyncio
import time
from typing import Dict, Any, Tuple

_CACHE: Dict[Tuple[str, int], Tuple[float, Dict[str, Any]]] = {}
_CACHE_TTL = 90.0  # seconds


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

    def _sync_search():
        try:
            from duckduckgo_search import DDGS
            results = []
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=max_results, safesearch="moderate"):
                    results.append({
                        "title": r.get("title", ""),
                        "url": r.get("href", ""),
                        "snippet": (r.get("body", "") or "")[:400],
                    })
            return results
        except Exception as e:
            return [{"error": f"Search failed: {type(e).__name__}: {e}"}]

    loop = asyncio.get_event_loop()
    results = await loop.run_in_executor(None, _sync_search)
    out = {"query": query, "count": len(results), "results": results, "cached": False}
    _CACHE[key] = (now, out)
    # Trim cache
    if len(_CACHE) > 200:
        oldest = sorted(_CACHE.items(), key=lambda kv: kv[1][0])[:100]
        for k, _ in oldest:
            _CACHE.pop(k, None)
    return out
