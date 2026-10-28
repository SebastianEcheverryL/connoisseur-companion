"""Tavily web search (free tier): reviews and atmosphere context for places found on OSM."""
import httpx
from pydantic import BaseModel

from connoisseur.log import get_logger
from connoisseur.observability import traced
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


def _page_text(raw: str) -> str:
    """Page text with one line per block: list items stay apart, runs of spaces collapse."""
    return "\n".join(" ".join(line.split()) for line in raw.splitlines() if line.strip())


@traced("tavily_search", as_type="tool")
async def search(
    query: str,
    max_results: int = 5,
    include_domains: list[str] | None = None,
    ttl_s: int | None = None,
    max_chars: int = MAX_SNIPPET_CHARS,
    raw: bool = False,
) -> list[WebSnippet]:
    """Run one basic Tavily search. Returns [] when no API key is configured or the call fails.

    With `raw`, `content` is the page text (line breaks kept) instead of the short snippet: a recipe's
    ingredients and steps do not fit in a snippet.
    """
    s = get_settings()
    if s.tavily_api_key is None:
        return []
    query = query.strip()[:300]
    cache = get_cache()
    key = make_key("tavily", {"q": query.lower(), "n": max_results, "d": include_domains or [], "c": max_chars,
                              "r": raw})
    if (hit := cache.get(key)) is not None:
        return [WebSnippet.model_validate(x) for x in hit]

    payload = {"query": query, "search_depth": "basic", "max_results": max_results, "include_answer": False}
    if include_domains:
        payload["include_domains"] = include_domains
    if raw:
        payload["include_raw_content"] = True
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
            content=_page_text(r.get("raw_content")) if raw and r.get("raw_content")
            else " ".join((r.get("content") or "").split()),
        )
        for r in results
        if r.get("url")
    ]
    snippets = [x.model_copy(update={"content": x.content[:max_chars]}) for x in snippets]
    if snippets:  # an empty answer can be a hiccup: caching it hid real results for a day
        cache.set(key, [x.model_dump() for x in snippets], ttl_s or s.cache_ttl_s)
    return snippets
