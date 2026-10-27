"""What to order at a specific restaurant, from web pages about it (menus, reviews, food blogs).

Same rules as place discovery (web_places.py): pages that do not name the restaurant are skipped,
pages that look like prompt injection never reach the LLM, and a dish is kept only when it appears
literally in the page it cites. Without that last check the model answered with the typical dishes
of the cuisine ("ajiaco, bandeja paisa") instead of the restaurant's own.
"""
from pydantic import BaseModel

from connoisseur.log import get_logger
from connoisseur.observability import traced
from connoisseur.places import tavily
from connoisseur.places.ratings import about_place, fold, site_name
from connoisseur.schemas import DishExtraction
from connoisseur.security import spotlight
from connoisseur.settings import get_settings

log = get_logger(__name__)

# Menus and reviews name dishes after a long header: keep more text than a rating lookup needs
DISH_CHARS = 1500


class Dish(BaseModel):
    name: str
    site: str
    url: str


def dishes_query(restaurant: str, area: str | None) -> str:
    return " ".join(filter(None, [f'"{restaurant}"', area, "menu dishes what to order"]))


def ground_dishes(extraction: DishExtraction | None, results: list[tavily.WebSnippet],
                  restaurant: str, limit: int) -> list[Dish]:
    """Keep the dishes that appear in the result they cite; one entry per dish, in result order."""
    if extraction is None:
        return []
    dishes, seen = [], set()
    for group in extraction.results:
        if not 0 <= group.result < len(results):
            continue
        source = results[group.result]
        text = fold(f"{source.title} {source.content}")
        for raw in group.dishes:
            name = " ".join(raw.split()).strip(" .,;:-")[:80]
            key = fold(name)
            # The restaurant's own name is not a dish ("Kai Sushi Bar" -> "Kai")
            if len(key) < 3 or key in seen or key in fold(restaurant):
                continue
            if key not in text:
                log.info("dish_rejected", reason="not in cited result")
                continue
            seen.add(key)
            dishes.append(Dish(name=name, site=site_name(source.url), url=source.url))
            if len(dishes) >= limit:
                return dishes
    return dishes


@traced("find_dishes", as_type="tool")
async def find_dishes(restaurant: str, area: str | None) -> list[Dish]:
    """Dishes that web pages about this restaurant name. [] when Tavily is off or nothing is found."""
    from connoisseur.agents.nodes import structured_call  # local import: nodes imports this package
    from connoisseur.agents.prompts import load_prompt

    s = get_settings()
    if not tavily.is_enabled():
        return []
    found = await tavily.search(dishes_query(restaurant, area), max_results=s.dish_search_results,
                                ttl_s=s.rating_cache_ttl_s, max_chars=DISH_CHARS)
    results, blocks = [], []
    for result in found:
        if not about_place(result, restaurant, area):
            continue  # another place, or a branch in another city
        safe, blocked = spotlight.sanitize(f"{result.title}\n{result.content}")
        if blocked:
            log.warning("web_result_blocked", site=site_name(result.url))
            continue
        blocks.append(f"Result {len(results)}:\n" + spotlight.tag(safe, source=site_name(result.url)))
        results.append(result)
    if not results:
        log.info("dishes_found", results=0, dishes=0)
        return []

    user = f"Restaurant: {restaurant}\nCity: {area or 'unknown'}\n\n" + "\n".join(blocks)
    extraction = await structured_call(DishExtraction, load_prompt("dishes"), user)
    dishes = ground_dishes(extraction, results, restaurant, s.max_dishes)
    log.info("dishes_found", results=len(results), dishes=len(dishes))
    return dishes
