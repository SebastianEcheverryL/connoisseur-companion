"""Research ratings for places found on OpenStreetMap, using Tavily web search.

Ratings are extracted with regular expressions, never by the LLM: a number is only accepted when
the snippet mentions the place by name and the value is a plausible rating. Each rating keeps its
source site so the answer can cite it.
"""
import asyncio
import re
import unicodedata
from urllib.parse import urlparse

from pydantic import BaseModel

from connoisseur.log import get_logger
from connoisseur.observability import traced
from connoisseur.places import tavily
from connoisseur.schemas import PlaceCandidate
from connoisseur.settings import get_settings

log = get_logger(__name__)

NUM = r"(\d{1,2}(?:[.,]\d{1,2})?)"
# Patterns seen in review-site snippets, e.g. "4.5 of 5 bubbles", "Rating: 4.6", "4,7 estrellas", "8.9/10"
RATING_PATTERNS = [
    (re.compile(rf"{NUM}\s*(?:/|out of|of|de)\s*5\b", re.IGNORECASE), 5.0),
    (re.compile(rf"{NUM}\s*(?:/|out of|of|de)\s*10\b", re.IGNORECASE), 10.0),
    (re.compile(rf"{NUM}\s*(?:stars?|estrellas|bubbles|burbujas)\b", re.IGNORECASE), 5.0),
    (re.compile(rf"(?:rating|rated|calificaci[oó]n|puntuaci[oó]n)\s*(?:of|:|de)?\s*{NUM}", re.IGNORECASE), 5.0),
]
REVIEWS_PATTERN = re.compile(r"(\d{1,3}(?:[.,]\d{3})*|\d+)\s*(?:reviews|opiniones|reseñas|ratings)\b", re.IGNORECASE)

SITE_NAMES = {
    "tripadvisor": "Tripadvisor",
    "restaurantguru": "Restaurant Guru",
    "yelp": "Yelp",
    "google": "Google",
    "foursquare": "Foursquare",
    "facebook": "Facebook",
    "rappi": "Rappi",
    "ubereats": "Uber Eats",
}


class PlaceRating(BaseModel):
    rating: float
    sources: list[str]
    url: str
    review_count: int | None = None
    snippet: str = ""


def fold(text: str) -> str:
    """Lowercase and strip accents so "Café" matches "cafe"."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def site_name(url: str) -> str:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    for key, name in SITE_NAMES.items():
        if key in host:
            return name
    return host


def extract_rating(text: str) -> float | None:
    """Return a rating normalised to a 0-5 scale, or None when no plausible rating is found."""
    for pattern, scale in RATING_PATTERNS:
        for match in pattern.finditer(text):
            value = float(match.group(1).replace(",", "."))
            if 0 < value <= scale:
                return round(value * 5 / scale, 1)
    return None


def extract_review_count(text: str) -> int | None:
    match = REVIEWS_PATTERN.search(text)
    if not match:
        return None
    digits = re.sub(r"[.,]", "", match.group(1))
    return int(digits) if digits.isdigit() else None


def rating_from_snippets(place_name: str, snippets: list[tavily.WebSnippet]) -> PlaceRating | None:
    """Combine ratings from snippets that mention the place; one value per source site."""
    name = fold(place_name)
    by_site: dict[str, tuple[float, tavily.WebSnippet]] = {}
    for snip in snippets:
        text = f"{snip.title} {snip.content}"
        if name not in fold(text):
            continue  # a rating for a different restaurant on the same page is worse than none
        rating = extract_rating(text)
        site = site_name(snip.url)
        if rating is not None and site not in by_site:
            by_site[site] = (rating, snip)
    if not by_site:
        return None

    ratings = [r for r, _ in by_site.values()]
    first_snip = next(iter(by_site.values()))[1]
    counts = [c for _, s in by_site.values() if (c := extract_review_count(f"{s.title} {s.content}"))]
    return PlaceRating(
        rating=round(sum(ratings) / len(ratings), 1),
        sources=list(by_site),
        url=first_snip.url,
        review_count=max(counts) if counts else None,
        snippet=first_snip.content[:200],
    )


async def research_place(place: PlaceCandidate, area: str | None) -> PlaceRating | None:
    s = get_settings()
    query = f'"{place.name}" {area or ""} restaurant rating reviews'.strip()
    snippets = await tavily.search(
        query, max_results=5, include_domains=s.rating_domains or None, ttl_s=s.rating_cache_ttl_s
    )
    return rating_from_snippets(place.name, snippets)


@traced("research_ratings", as_type="tool")
async def enrich_with_ratings(places: list[PlaceCandidate], area: str | None) -> list[PlaceCandidate]:
    """Look up ratings for the top places (in parallel), then re-rank with the rating as a signal."""
    s = get_settings()
    if not tavily.is_enabled() or not places:
        return places
    top = [p for p in places if p.rating is None][: s.rating_lookups]  # web finds may come rated already
    results = await asyncio.gather(*(research_place(p, area) for p in top), return_exceptions=True)

    found = 0
    for place, result in zip(top, results, strict=True):
        if isinstance(result, PlaceRating):
            found += 1
            place.rating = result.rating
            place.rating_source = " + ".join(result.sources)
            place.review_count = result.review_count
            place.review_url = result.url
            place.review_snippet = result.snippet
            # Good ratings lift a place; poor ones push it down (3/5 is neutral)
            place.score = round(place.score + (result.rating - 3.0) * 0.5, 3)
    log.info("ratings_enriched", looked_up=len(top), found=found)
    return sorted(places, key=lambda p: p.score, reverse=True)
