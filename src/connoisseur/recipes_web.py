"""Recipes from the web (Tavily) for dishes the dataset does not have ("busca una receta de ajiaco").

Same defences as the web place and dish lookups: only pages whose title or address name the dish are
read, a page that looks like prompt injection never reaches the LLM, and the extracted recipe is checked
against the page in code. An ingredient or step that shares no word with the page is dropped, and a
recipe left with fewer than three ingredients or two steps is not shown.
"""
import math
import re

from pydantic import BaseModel

from connoisseur.log import get_logger
from connoisseur.observability import traced
from connoisseur.places import tavily
from connoisseur.places.ratings import fold, site_name
from connoisseur.schemas import WebRecipe
from connoisseur.security import spotlight
from connoisseur.settings import get_settings

log = get_logger(__name__)

MIN_INGREDIENTS = 3
MIN_STEPS = 2  # Nova Lite once returned a single cut-off step for a whole ajiaco
MAX_INGREDIENTS = 25
MAX_STEPS = 15
# Words every recipe page has: they cannot prove that an ingredient or a step comes from the page
FILLER = {
    "taza", "tazas", "cucharada", "cucharadas", "cucharadita", "cucharaditas", "gramos", "libra", "libras",
    "pizca", "unidad", "unidades", "grande", "grandes", "picado", "picada", "fresco", "fresca", "gusto",
    "cups", "tablespoon", "tablespoons", "teaspoon", "teaspoons", "grams", "pound", "pounds", "ounce", "ounces",
    "pinch", "large", "small", "fresh", "chopped", "minutes", "minutos", "hasta", "until", "luego", "then",
    "agregar", "anadir", "with", "para", "durante",
}


class FoundRecipe(BaseModel):
    name: str
    ingredients: list[str]
    steps: list[str]
    site: str
    url: str


def recipe_query(dish: str, language: str) -> str:
    return f"receta de {dish}" if language.strip().lower().startswith("span") else f"{dish} recipe"


def names_dish(page: tavily.WebSnippet, dish: str) -> bool:
    """The page's title or address names most words of the dish ("ajiaco" in "Ajiaco santafereño")."""
    words = [w for w in re.findall(r"\w+", fold(dish)) if len(w) > 2]
    heading = fold(f"{page.title} {page.url}").replace("-", " ").replace("_", " ")
    return bool(words) and sum(w in heading for w in words) >= math.ceil(len(words) / 2)


def _key_words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z]+", fold(text)) if len(w) >= 4 and w not in FILLER]


def _on_page(text: str, page_text: str) -> bool:
    return any(w in page_text for w in _key_words(text))


def ground_recipe(extraction: WebRecipe | None, page: tavily.WebSnippet) -> FoundRecipe | None:
    """Keep what the page supports; None when too little is left to cook from."""
    if extraction is None:
        return None
    page_text = fold(f"{page.title}\n{page.content}")
    ingredients = [" ".join(i.split()) for i in extraction.ingredients if i.strip() and _on_page(i, page_text)]
    steps = [" ".join(s.split()) for s in extraction.steps if s.strip() and _on_page(s, page_text)]
    dropped = len(extraction.ingredients) - len(ingredients) + len(extraction.steps) - len(steps)
    if dropped:
        log.info("web_recipe_lines_dropped", count=dropped)
    if len(ingredients) < MIN_INGREDIENTS or len(steps) < MIN_STEPS:
        return None
    return FoundRecipe(name=" ".join(extraction.name.split())[:100] or page.title[:100],
                       ingredients=ingredients[:MAX_INGREDIENTS], steps=steps[:MAX_STEPS],
                       site=site_name(page.url), url=page.url)


@traced("find_web_recipe", as_type="tool")
async def find_recipe(dish: str, language: str) -> FoundRecipe | None:
    """A recipe for `dish` from the first web page that names it and passes the checks."""
    from connoisseur.agents.nodes import structured_call  # local import: nodes imports this module
    from connoisseur.agents.prompts import load_prompt

    s = get_settings()
    if not tavily.is_enabled() or not dish.strip():
        return None
    pages = await tavily.search(recipe_query(dish, language), max_results=s.web_recipe_results,
                                ttl_s=s.rating_cache_ttl_s, max_chars=s.web_recipe_chars, raw=True)
    about = [page for page in pages if names_dish(page, dish)]
    for page in about[:2]:  # one LLM call per page: stop at the first usable recipe
        safe, blocked = spotlight.sanitize(f"{page.title}\n{page.content}")
        if blocked:
            log.warning("web_result_blocked", site=site_name(page.url))
            continue
        user = f"Dish: {dish}\n\n" + spotlight.tag(safe, source=site_name(page.url))
        if found := ground_recipe(await structured_call(WebRecipe, load_prompt("web_recipe"), user), page):
            log.info("web_recipe", pages=len(pages), about=len(about), found=True,
                     ingredients=len(found.ingredients), steps=len(found.steps))
            return found
    log.info("web_recipe", pages=len(pages), about=len(about), found=False)
    return None
