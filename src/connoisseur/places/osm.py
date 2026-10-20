"""OpenStreetMap lookups: Nominatim (geocoding) and Overpass (restaurants around a point).

Both services are free community infrastructure, so every call is cached, identifies the app
with a User-Agent, and Nominatim calls are throttled to 1 request per second as its policy asks.
"""
import asyncio
import math
import time

import httpx

from connoisseur.log import get_logger
from connoisseur.places.cache import get_cache, make_key
from connoisseur.schemas import Coordinates, PlaceCandidate, ResolvedLocation
from connoisseur.settings import get_settings

log = get_logger(__name__)

_nominatim_lock = asyncio.Lock()
_last_nominatim_call = 0.0

# User wording -> OSM diet:* key
DIET_TAGS = {
    "vegan": "vegan",
    "vegetarian": "vegetarian",
    "gluten-free": "gluten_free",
    "gluten free": "gluten_free",
    "celiac": "gluten_free",
    "halal": "halal",
    "kosher": "kosher",
    "lactose-free": "lactose_free",
    "dairy-free": "lactose_free",
}


def _headers() -> dict:
    return {"User-Agent": get_settings().osm_user_agent, "Accept-Language": "en"}


async def _nominatim_get(path: str, params: dict) -> list | dict | None:
    global _last_nominatim_call
    s = get_settings()
    async with _nominatim_lock:
        wait = 1.0 - (time.monotonic() - _last_nominatim_call)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            async with httpx.AsyncClient(timeout=s.http_timeout_s, headers=_headers()) as client:
                resp = await client.get(f"{s.nominatim_url}{path}", params={**params, "format": "jsonv2"})
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as exc:
            log.warning("nominatim_error", path=path, error=str(exc))
            return None
        finally:
            _last_nominatim_call = time.monotonic()


def _to_location(query: str, item: dict) -> ResolvedLocation:
    address = item.get("address") or {}
    city = address.get("city") or address.get("town") or address.get("village") or address.get("suburb")
    return ResolvedLocation(
        query=query,
        display_name=item.get("display_name", query),
        coordinates=Coordinates(lat=float(item["lat"]), lon=float(item["lon"])),
        city=city,
        state=address.get("state"),
        country_code=address.get("country_code"),
    )


async def geocode(query: str) -> ResolvedLocation | None:
    """Resolve a free-text place ("El Poblado, Medellín") to coordinates."""
    query = query.strip()[:200]
    if not query:
        return None
    s, cache = get_settings(), get_cache()
    key = make_key("geocode", {"q": query.lower()})
    if (hit := cache.get(key)) is not None:
        return ResolvedLocation.model_validate(hit)

    items = await _nominatim_get("/search", {"q": query, "addressdetails": 1, "limit": 1})
    if not items:
        return None
    location = _to_location(query, items[0])
    cache.set(key, location.model_dump(), s.cache_ttl_s * 30)  # places do not move: cache for a month
    return location


async def reverse_geocode(coords: Coordinates) -> ResolvedLocation | None:
    """Turn browser coordinates (already rounded to ~1 km) into a neighbourhood and city."""
    s, cache = get_settings(), get_cache()
    key = make_key("reverse", coords.model_dump())
    if (hit := cache.get(key)) is not None:
        return ResolvedLocation.model_validate(hit)

    item = await _nominatim_get(
        "/reverse", {"lat": coords.lat, "lon": coords.lon, "zoom": 14, "addressdetails": 1}
    )
    if not item or "lat" not in item:
        return None
    location = _to_location(f"{coords.lat},{coords.lon}", item)
    cache.set(key, location.model_dump(), s.cache_ttl_s * 30)
    return location


def haversine_m(a: Coordinates, b: Coordinates) -> float:
    r = 6_371_000
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp, dl = p2 - p1, math.radians(b.lon - a.lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _overpass_query(center: Coordinates, radius_m: int, limit: int) -> str:
    return (
        "[out:json][timeout:20];"
        f'nwr(around:{radius_m},{center.lat},{center.lon})["amenity"~"^(restaurant|cafe|fast_food)$"]["name"];'
        f"out center tags {limit};"
    )


def _diet_tags(tags: dict) -> list[str]:
    return sorted({key.split(":", 1)[1] for key, val in tags.items()
                   if key.startswith("diet:") and val in ("yes", "only")})


def element_to_candidate(element: dict, center: Coordinates, fallback_area: str | None) -> PlaceCandidate | None:
    tags = element.get("tags") or {}
    name = tags.get("name")
    lat = element.get("lat") or (element.get("center") or {}).get("lat")
    lon = element.get("lon") or (element.get("center") or {}).get("lon")
    if not name or lat is None or lon is None:
        return None
    coords = Coordinates(lat=lat, lon=lon)
    osm_ref = f"{element.get('type', 'node')}/{element.get('id')}"
    area = tags.get("addr:suburb") or tags.get("addr:city") or fallback_area
    street = " ".join(filter(None, [tags.get("addr:street"), tags.get("addr:housenumber")]))
    details = [tags.get("amenity", "restaurant")]
    if tags.get("opening_hours"):
        details.append(f"opening hours: {tags['opening_hours']}")
    return PlaceCandidate(
        id=f"osm:{osm_ref}",
        name=name,
        source="osm",
        location=", ".join(filter(None, [street, area])) or None,
        cuisine=(tags.get("cuisine") or "").replace(";", ", ").replace("_", " ") or None,
        description="; ".join(details),
        diet_tags=_diet_tags(tags),
        coordinates=coords,
        url=tags.get("website") or f"https://www.openstreetmap.org/{osm_ref}",
        score=-haversine_m(center, coords),  # provisional: replaced by rank_places
    )


def rank_places(places: list[PlaceCandidate], cuisines: list[str], dietary: list[str],
                center: Coordinates, radius_m: int) -> list[PlaceCandidate]:
    """Score OSM places: cuisine and diet matches first, distance as a tie-breaker."""
    wanted_diets = {DIET_TAGS[d.lower()] for d in dietary if d.lower() in DIET_TAGS}
    wanted_cuisines = [c.lower() for c in cuisines]
    for place in places:
        score = 0.0
        cuisine = (place.cuisine or "").lower()
        if wanted_cuisines and any(c in cuisine or cuisine in c for c in wanted_cuisines if cuisine):
            score += 2.0
        score += 2.0 * len(wanted_diets & set(place.diet_tags))
        if place.coordinates:
            score += 1.0 - min(haversine_m(center, place.coordinates) / max(radius_m, 1), 1.0)
        place.score = round(score, 3)
    return sorted(places, key=lambda p: p.score, reverse=True)


RETRYABLE_STATUS = {429, 502, 503, 504}


async def _overpass_fetch(query: str, attempts_per_server: int = 2) -> list[dict] | None:
    """POST the query to each configured Overpass server, backing off on overload responses."""
    s = get_settings()
    async with httpx.AsyncClient(timeout=s.http_timeout_s, headers=_headers()) as client:
        for url in s.overpass_urls:
            for attempt in range(attempts_per_server):
                try:
                    resp = await client.post(url, data={"data": query})
                    if resp.status_code in RETRYABLE_STATUS:
                        log.info("overpass_busy", url=url, status=resp.status_code, attempt=attempt)
                        await asyncio.sleep(0.5 * 2**attempt)
                        continue
                    resp.raise_for_status()
                    return resp.json().get("elements", [])
                except (httpx.HTTPError, ValueError) as exc:
                    log.warning("overpass_error", url=url, error_type=type(exc).__name__, error=str(exc))
                    break  # network or parse error: move to the next server
    return None


async def find_places(
    center: Coordinates,
    cuisines: list[str] | None = None,
    dietary: list[str] | None = None,
    radius_m: int | None = None,
    area_name: str | None = None,
    limit: int | None = None,
) -> list[PlaceCandidate]:
    """Restaurants, cafés and fast-food places with a name within `radius_m` of `center`."""
    s, cache = get_settings(), get_cache()
    radius_m = min(radius_m or s.web_search_radius_m, 10_000)
    limit = limit or s.web_max_places
    center = center.rounded(3)  # ~100 m: improves cache hits without losing relevance
    key = make_key("overpass", {"c": center.model_dump(), "r": radius_m})

    elements = cache.get(key)
    if elements is None:
        elements = await _overpass_fetch(_overpass_query(center, radius_m, 80))
        if elements is None:
            return []
        cache.set(key, elements, s.cache_ttl_s)

    places = [p for e in elements if (p := element_to_candidate(e, center, area_name))]
    return rank_places(places, cuisines or [], dietary or [], center, radius_m)[:limit]
