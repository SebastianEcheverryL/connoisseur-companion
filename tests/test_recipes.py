"""Recipe follow-ups and web recipes: "¿cómo preparo la segunda?" and "receta de ajiaco" no longer
re-run the whole search or answer with unrelated dishes."""
import json

import pytest
from tests.conftest import RECIPES_FIXTURE

from connoisseur import memory, recipes_web
from connoisseur.agents import nodes
from connoisseur.places import tavily
from connoisseur.places.tavily import WebSnippet
from connoisseur.schemas import UserContext, WebRecipe
from connoisseur.security import spotlight
from connoisseur.security.guardrails import GuardrailResult

with open(RECIPES_FIXTURE, encoding="utf-8") as f:
    RECIPES = {r["name"]: r for r in json.load(f)}
CARDS = [memory.CardRef(n=1, type="restaurant", id="web:a", name="Della Nonna"),
         memory.CardRef(n=1, type="recipe", id=f"recipe:{RECIPES['Spaghetti Aglio e Olio']['id']}",
                        name="Spaghetti Aglio e Olio"),
         memory.CardRef(n=2, type="recipe", id=f"recipe:{RECIPES['Pasta Arrabbiata']['id']}",
                        name="Pasta Arrabbiata")]


def state(message, dish=None, cards=CARDS, intent="recipe", language="Spanish", allergies=()):
    known = memory.Profile(allergies=list(allergies)) if allergies else None
    return {"context": UserContext(intent=intent, language=language, dish=dish, search_query=dish or ""),
            "message": message, "last_cards": list(cards), "route": [f"intent:{intent}"], "memory": known}


@pytest.mark.parametrize("text", ["¿Cómo preparo la segunda?", "how do I make it?", "dame los pasos",
                                  "¿qué ingredientes lleva?", "¿cómo se hace?", "quiero cocinarla"])
def test_how_to_cook_questions(text):
    assert nodes.asks_how_to_cook(text)


@pytest.mark.parametrize("text", ["Dame una receta fácil de pasta", "¿qué pido en el segundo?"])
def test_other_messages_are_not_how_to_cook(text):
    assert not nodes.asks_how_to_cook(text)


def test_local_recipe_matching_allows_spelling_variants():
    assert [r["name"] for r in nodes._local_recipes("pizza margarita")] == ["Classic Margherita Pizza"]
    assert [r["name"] for r in nodes._local_recipes("chicken tikka masala")] == ["Chicken Tikka Masala"]
    assert [r["name"] for r in nodes._local_recipes("pollo tikka masala")] == ["Chicken Tikka Masala"]
    assert nodes._local_recipes("chicken soup") == []  # one word of two is not enough
    assert len(nodes._local_recipes("pasta")) > 1  # a search, not one recipe
    assert nodes._local_recipes("ajiaco") == []


def test_recipe_cards_by_position_name_and_only_one():
    assert nodes._previous_card(state("¿Cómo preparo la segunda?"), "recipe").name == "Pasta Arrabbiata"
    assert nodes._previous_card(state("¿cómo hago la pasta arrabbiata?"), "recipe").name == "Pasta Arrabbiata"
    assert nodes._previous_card(state("pasos", dish="Spaghetti Aglio e Olio"), "recipe").n == 1
    # "el primero" on restaurants still means the restaurant
    assert nodes._previous_card(state("¿qué pido en el primero?", intent="lookup"), "restaurant").name == "Della Nonna"
    assert nodes._previous_card(state("¿cómo la preparo?", cards=CARDS[:2]), "recipe").n == 1


@pytest.mark.parametrize(("message", "dish", "tavily_on", "expected"), [
    ("¿Cómo preparo la segunda?", None, True, "recipe_detail"),
    ("¿Cómo preparo pizza margarita?", "pizza margarita", True, "recipe_detail"),
    ("Dame una receta de ajiaco", "ajiaco", True, "web_recipe"),
    ("Busca en internet una receta de chicken tikka masala", "chicken tikka masala", True, "web_recipe"),
    ("Dame una receta de ajiaco", "ajiaco", False, "retrieve_local"),  # no Tavily: the dataset is all we have
    ("Dame una receta fácil de pasta", "pasta", True, "retrieve_local"),
    ("Dame una receta fácil", None, True, "retrieve_local"),
])
def test_recipe_routing(monkeypatch, message, dish, tavily_on, expected):
    monkeypatch.setattr(tavily, "is_enabled", lambda: tavily_on)
    assert nodes.route_intent(state(message, dish=dish, cards=[] if dish else CARDS)) == expected


def test_a_lookup_about_a_shown_recipe_goes_to_its_steps():
    assert nodes.route_intent(state("¿Cómo preparo la segunda?", intent="lookup")) == "recipe_detail"


async def test_recipe_detail_gives_that_recipes_ingredients_and_directions():
    out = await nodes.recipe_detail(state("¿Cómo preparo la segunda?"))
    recipe = RECIPES["Pasta Arrabbiata"]
    assert out["answer"].startswith("**Pasta Arrabbiata**")
    assert "**Ingredientes**" in out["answer"] and "**Preparación**" in out["answer"]
    assert all(step in out["answer"] for step in recipe["directions"])
    assert out["route"] == ["intent:recipe", "recipe_detail"] and out["cards"][0]["name"] == "Pasta Arrabbiata"


async def test_recipe_detail_warns_when_the_recipe_has_an_allergen():
    shrimp = state("¿cómo la preparo?", dish="Lemon Garlic Shrimp Pasta", cards=[], allergies=["shellfish"])
    out = await nodes.recipe_detail(shrimp)
    assert "⚠️ **Esta receta lleva ingredientes" in out["answer"]


async def test_recipe_detail_asks_which_recipe_when_unclear():
    out = await nodes.recipe_detail(state("¿cómo la preparo?", cards=[]))
    assert out["answer"].startswith("¿Cuál receta?") and out["route"][-1] == "recipe_detail:none"


# ---------- web recipes ----------

PAGE = WebSnippet(
    title="Ajiaco santafereño - Receta tradicional", url="https://www.recetasgratis.net/ajiaco",
    content="Ajiaco santafereño\nIngredientes\n1 kg de pechuga de pollo\n1 kg de papa criolla\n500 g de papa sabanera\n"
            "3 mazorcas\n1 manojo de guascas\nPreparación\nCocinar el pollo con las mazorcas en agua.\n"
            "Agregar las papas y las guascas y cocinar 40 minutos.\nServir con crema de leche y alcaparras.",
)
OTHER = WebSnippet(title="Sancocho de gallina", url="https://x.example/sancocho", content="Sancocho ...")


def extracted(ingredients, steps=("Cocinar el pollo con las mazorcas.", "Agregar las papas y las guascas.")):
    return WebRecipe(name="Ajiaco santafereño", ingredients=list(ingredients), steps=list(steps))


def test_web_recipe_keeps_only_what_the_page_says():
    found = recipes_web.ground_recipe(extracted(["1 kg de pechuga de pollo", "1 kg de papa criolla", "3 mazorcas",
                                                 "1 manojo de guascas", "2 tazas de quinua"]), PAGE)
    assert "2 tazas de quinua" not in found.ingredients and len(found.ingredients) == 4  # quinua is not on the page
    assert found.site == "recetasgratis.net" and found.url == PAGE.url


def test_thin_or_empty_web_recipes_are_rejected():
    assert recipes_web.ground_recipe(extracted(["1 kg de pechuga de pollo", "2 tazas de quinua"]), PAGE) is None
    assert recipes_web.ground_recipe(extracted(["pollo", "papa criolla", "mazorcas"], steps=[]), PAGE) is None
    one_step = extracted(["pollo", "papa criolla", "mazorcas"], steps=["Cocinar el pollo con las mazorcas."])
    assert recipes_web.ground_recipe(one_step, PAGE) is None
    assert recipes_web.ground_recipe(None, PAGE) is None


def test_pages_must_name_the_dish():
    assert recipes_web.names_dish(PAGE, "ajiaco")
    assert not recipes_web.names_dish(OTHER, "ajiaco")
    assert recipes_web.recipe_query("ajiaco", "Spanish") == "receta de ajiaco"
    assert recipes_web.recipe_query("ajiaco", "English") == "ajiaco recipe"


@pytest.fixture
def web(monkeypatch):
    calls = {"queries": [], "extract": [],
             "answer": extracted(["1 kg de pechuga de pollo", "1 kg de papa criolla", "3 mazorcas"])}

    async def search(query, **kwargs):
        calls["queries"].append((query, kwargs.get("raw")))
        return [OTHER, PAGE]

    async def structured_call(schema, system, user):
        calls["extract"].append(user)
        return calls["answer"]

    monkeypatch.setattr(tavily, "is_enabled", lambda: True)
    monkeypatch.setattr(tavily, "search", search)
    monkeypatch.setattr(nodes, "structured_call", structured_call)
    return calls


async def test_web_recipe_answer_lists_ingredients_steps_and_source(web):
    out = await nodes.web_recipe(state("Dame una receta de ajiaco", dish="ajiaco", cards=[]))
    assert web["queries"] == [("receta de ajiaco", True)]
    assert "Sancocho" not in web["extract"][0] and "<untrusted_data" in web["extract"][0]
    assert out["answer"].startswith("**Ajiaco santafereño** (recetasgratis.net)")
    assert "- 3 mazorcas" in out["answer"] and "1. Cocinar el pollo" in out["answer"]
    assert "[recetasgratis.net](https://www.recetasgratis.net/ajiaco)" in out["answer"]
    assert out["route"][-1] == "web_recipe" and out["cards"][0]["source"] == "web"
    assert nodes.route_after_web_recipe(out) == "output_guard"


async def test_no_usable_web_recipe_falls_back_to_the_dataset_saying_so(web):
    web["answer"] = extracted(["2 tazas de quinua"])
    out = await nodes.web_recipe(state("Dame una receta de ajiaco", dish="ajiaco", cards=[]))
    assert "answer" not in out and out["route"][-1] == "web_recipe:none"
    assert out["notice"].startswith("No encontré en la web una receta confiable de ajiaco")
    assert nodes.route_after_web_recipe(out) == "retrieve_local"


async def test_injected_recipe_pages_never_reach_the_llm(web, monkeypatch):
    monkeypatch.setattr(spotlight, "check_untrusted",
                        lambda text: GuardrailResult(allowed="guascas" not in text, stage="untrusted_content"))
    assert await recipes_web.find_recipe("ajiaco", "Spanish") is None
    assert web["extract"] == []
