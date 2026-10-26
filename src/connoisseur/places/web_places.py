"""Place discovery on the web (Tavily) for when OpenStreetMap has nothing: public Overpass servers are
often overloaded, and Photon only matches names.

One Tavily search over review and listing sites, then an LLM lists the restaurant names it finds.
The LLM never decides what is true: a name is kept only when it appears literally in the result it
cites, results that look like prompt injection are dropped before the LLM sees them, and ratings are
parsed with regular expressions from results about that one place.
"""
import hashlib

from connoisseur.log import get_logger
from connoisseur.observability import traced
from connoisseur.places import tavily
from connoisseur.places.ratings import SITE_NAMES, fold, rating_from_snippets, site_name
from connoisseur.schemas import PlaceCandidate, ResolvedLocation, UserContext, WebPlaceExtraction
from connoisseur.security import spotlight
from connoisseur.settings import get_settings

log = get_logger(__name__)

DISCOVERY_CHARS = 1500

# Names that are sites or rankings, not restaurants
NOT_PLACES = {fold(name) for name in SITE_NAMES.values()} | {"restaurant", "restaurants", "best restaurants"}


def discovery_query(ctx: UserContext, area: str) -> str:
    wanted = ctx.search_query.strip() or " ".join(ctx.cuisines) or "restaurants"
    return f"best {wanted} restaurants in {area}"


def ground_places(extraction: WebPlaceExtraction | None, results: list[tavily.WebSnippet],
                  area: str, limit: int) -> list[PlaceCandidate]:
    """Keep the names that appear in the result they cite; one candidate per name, in result order."""
    if extraction is None:
        return []
    places, seen = [], set()
    cited = [(group.result, item) for group in extraction.results for item in group.places]
    for index, item in cited:
        if not 0 <= index < len(results):
            continue
        source = results[index]
        name = " ".join(item.name.split())[:80]
        key = fold(name)
        if len(key) < 2 or key in NOT_PLACES or key == fold(area):
            continue
        # Listings name the same place in several ways ("ONI Nikkei", "ONI Nikkei - sushi medellin")
        if any(key.startswith(other) or other.startswith(key) for other in seen):
            continue
        if key not in fold(f"{source.title} {source.content}"):
            log.info("web_place_rejected", reason="not in cited result")
            continue
        seen.add(key)
        # Only results about this place can rate it: a ranking page lists ratings of many restaurants
        about = [r for r in results if key in fold(r.title)]
        rating = rating_from_snippets(name, about)
        places.append(PlaceCandidate(
            id="web:" + hashlib.sha256(f"{key}|{fold(area)}".encode()).hexdigest()[:16],
            name=name,
            source="web",
            location=area,
            cuisine=item.cuisine or None,
            description=f"Mentioned on {site_name(source.url)}",
            url=source.url,
            rating=rating.rating if rating else None,
            rating_source=" + ".join(rating.sources) if rating else None,
            review_count=rating.review_count if rating else None,
            review_url=rating.url if rating else source.url,
            review_snippet=rating.snippet if rating else source.content[:200],
            score=-len(places),  # keep the order the results gave
        ))
        if len(places) >= limit:
            break
    return places


@traced("discover_places", as_type="tool")
async def discover_places(location: ResolvedLocation, ctx: UserContext) -> list[PlaceCandidate]:
    """Restaurants named on review and listing sites for this request and city."""
    from connoisseur.agents.nodes import structured_call  # local import: nodes imports this package
    from connoisseur.agents.prompts import load_prompt

    s = get_settings()
    if not tavily.is_enabled():
        return []
    area = location.city or location.query
    # Listings put the names after a long header: keep more text than a rating lookup needs
    found = await tavily.search(discovery_query(ctx, area), max_results=s.web_discovery_results,
                                include_domains=s.rating_domains or None, max_chars=DISCOVERY_CHARS)
    results, blocks = [], []
    for result in found:
        safe, blocked = spotlight.sanitize(f"{result.title}\n{result.content}")
        if blocked:
            log.warning("web_result_blocked", site=site_name(result.url))
            continue
        blocks.append(f"Result {len(results)}:\n" + spotlight.tag(safe, source=site_name(result.url)))
        results.append(result)
    if not results:
        log.info("web_discovery", results=0, places=0)
        return []

    listing = "\n".join(blocks)
    user = f"City: {area}\nThe user wants: {discovery_query(ctx, area)}\n\n{listing}"
    for _ in range(2):  # Nova Lite now and then answers with no names at all (1 run in 7): one retry
        extraction = await structured_call(WebPlaceExtraction, load_prompt("web_places"), user)
        extracted = sum(len(group.places) for group in extraction.results) if extraction else 0
        if extracted:
            break
    places = ground_places(extraction, results, area, s.web_max_places)
    log.info("web_discovery", results=len(results), extracted=extracted,
             places=len(places), rated=sum(p.rating is not None for p in places))
    return places
