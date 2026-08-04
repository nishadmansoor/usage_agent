"""Web search tool — real, citable external sources for the agent.

Backed by the Tavily Search API (https://tavily.com), which is built for AI agents
and returns clean results with titles, URLs, and dates — exactly what the
benchmarking analysis needs to cite sources.

Config (no secret in code):
    TAVILY_API_KEY   (required to enable web search)

If the key isn't set, the tool degrades gracefully: it returns a clear "not
configured" message so the agent flags external benchmarks as [Needs sourcing]
instead of failing or fabricating.

Governance note: search queries are sent to Tavily (a third party). Keep queries
generic (industry benchmarks), never internal figures or user data.
"""

from __future__ import annotations

import os

from ..logging_config import get_logger

logger = get_logger(__name__)

_TAVILY_URL = "https://api.tavily.com/search"


def web_search(query: str, *, max_results: int = 5) -> list[dict] | str:
    """Search the public web and return results to cite.

    Returns a list of ``{"title", "url", "published_date", "snippet"}`` dicts,
    or a plain string when search is unavailable/errors (so the agent can report
    it and treat the benchmark as [Needs sourcing]). Never raises for the model.
    """
    key = os.getenv("TAVILY_API_KEY")
    if not key:
        return (
            "web_search is not configured (TAVILY_API_KEY is not set). External "
            "sources cannot be retrieved — treat any external/peer benchmark as "
            "[Needs sourcing] and do NOT invent a number or URL."
        )

    q = (query or "").strip()
    if not q:
        return "web_search requires a non-empty query."

    n = max(1, min(int(max_results or 5), 10))
    # Lazy import so offline tests / non-network paths don't need httpx.
    import httpx

    body = {
        # Send the key both ways for compatibility across Tavily API versions.
        "api_key": key,
        "query": q,
        "max_results": n,
        "search_depth": "basic",
        "include_answer": False,
        "topic": "general",
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    logger.info("web_search(%r, max_results=%d)", q, n)
    try:
        resp = httpx.post(_TAVILY_URL, headers=headers, json=body, timeout=30.0)
    except Exception as exc:  # noqa: BLE001 - network/DNS/SSL failure
        return f"web_search failed to reach the search API ({exc}). Treat externals as [Needs sourcing]."

    if resp.is_error:
        return (
            f"web_search returned HTTP {resp.status_code}: {resp.text[:300]}. "
            "Treat externals as [Needs sourcing]."
        )

    results = (resp.json() or {}).get("results") or []
    if not results:
        return f"No web results found for {q!r}."

    return [
        {
            "title": r.get("title"),
            "url": r.get("url"),
            "published_date": r.get("published_date"),  # present for recent/news items
            "snippet": (r.get("content") or "")[:600],
        }
        for r in results
    ]
