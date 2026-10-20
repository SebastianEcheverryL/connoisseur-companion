"""Tavily web search (free tier): reviews and atmosphere context for places found on OSM."""
import httpx
from pydantic import BaseModel

from connoisseur.log import get_logger
from connoisseur.places.cache import get_cache, make_key
from connoisseur.settings import get_settings

log = get_logger(__name__)
MAX_SNIPPET_CHARS = 500


class WebSnippet(BaseModel):
    title: str
    url: str
    content: str


def is_enabled() -> bool:
    return get_settings().tavily_api_key is not None


async def search(query: str, max_results: int = 5) -> list[WebSnippet]:
    """Run one basic Tavily search. Returns [] when no API key is configured or the call fails."""
    s = get_settings()
    if s.tavily_api_key is None:
        return []
    query = query.strip()[:300]
    cache = get_cache()
    key = make_key("tavily", {"q": query.lower(), "n": max_results})
    if (hit := cache.get(key)) is not None:
        return [WebSnippet.model_validate(x) for x in hit]

    payload = {"query": query, "search_depth": "basic", "max_results": max_results, "include_answer": False}
    headers = {"Authorization": f"Bearer {s.tavily_api_key.get_secret_value()}"}
    try:
        async with httpx.AsyncClient(timeout=s.http_timeout_s) as client:
            resp = await client.post(s.tavily_url, json=payload, headers=headers)
            resp.raise_for_status()
            results = resp.json().get("results", [])
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("tavily_error", error=str(exc))
        return []

    snippets = [
        WebSnippet(
            title=(r.get("title") or "")[:200],
            url=r.get("url") or "",
            content=" ".join((r.get("content") or "").split())[:MAX_SNIPPET_CHARS],
        )
        for r in results
        if r.get("url")
    ]
    cache.set(key, [x.model_dump() for x in snippets], s.cache_ttl_s)
    return snippets
