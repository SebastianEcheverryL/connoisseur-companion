"""What to order at a named restaurant: dish grounding, the dish question detector and the recall branch."""
import pytest

from connoisseur import memory
from connoisseur.agents import nodes
from connoisseur.places import dishes, ratings, tavily
from connoisseur.places.tavily import WebSnippet
from connoisseur.schemas import DishExtraction, ResultDishes, UserContext
from connoisseur.security import spotlight
from connoisseur.security.guardrails import GuardrailResult

PAGES = [
    WebSnippet(title="Carmen, Medellín - Menu", url="https://www.carmenmedellin.com/menu",
               content="Carmen menu: Pulpo a la parrilla, Risotto de hongos, Cheesecake de maracuyá."),
    WebSnippet(title="Carmen Medellín reviews - Tripadvisor", url="https://www.tripadvisor.com/carmen",
               content="We had the risotto de hongos at Carmen and the tasting menu. 4.5 of 5"),
    WebSnippet(title="Best restaurants in Medellín", url="https://blog.example/medellin",
               content="El Cielo serves an edible tree and a liquid nitrogen dessert."),
]


def extraction(*groups):
    """(result number, [dishes]) pairs, as the model groups them."""
    return DishExtraction(results=[ResultDishes(result=r, dishes=list(d)) for r, d in groups])


def test_dishes_must_appear_in_the_result_they_cite():
    found = dishes.ground_dishes(
        extraction((0, ["Pulpo a la parrilla", "Ajiaco", "Bandeja paisa"]), (1, ["Risotto de hongos"])),
        PAGES, "Carmen", limit=6)
    # Typical Colombian dishes the pages do not name are dropped
    assert [d.name for d in found] == ["Pulpo a la parrilla", "Risotto de hongos"]
    assert found[0].url == "https://www.carmenmedellin.com/menu"
    assert found[1].site == "Tripadvisor"


def test_duplicates_bad_indexes_the_restaurant_name_and_the_limit():
    found = dishes.ground_dishes(
        extraction((0, ["Carmen", "pulpo a la parrilla.", "Pulpo a la Parrilla", "ok"]), (9, ["Pulpo"]),
                   (0, ["Risotto de hongos", "Cheesecake de maracuyá"])),
        PAGES, "Carmen", limit=2)
    assert [d.name for d in found] == ["pulpo a la parrilla", "Risotto de hongos"]
    assert dishes.ground_dishes(None, PAGES, "Carmen", limit=6) == []


@pytest.mark.parametrize("text", [
    "¿qué platos me recomiendas de Carmen?", "qué pido en el segundo", "¿cuál es su especialidad?",
    "what should I order at Kai?", "What dishes are good there?", "¿tienen menú vegano?",
    "¿Qué me recomiendas pedir en Hacienda Origen?", "what do people order there?",
])
def test_dish_questions_are_detected(text):
    assert nodes.asks_for_dishes(text)


@pytest.mark.parametrize("text", ["¿el primero tiene terraza?", "tell me more about the second one",
                                  "how are the reviews of Carmen?"])
def test_other_questions_are_not_dish_questions(text):
    assert not nodes.asks_for_dishes(text)


@pytest.fixture
def web(monkeypatch):
    calls = {"queries": [], "extract": [], "recall": [], "answer": extraction((0, ["Pulpo a la parrilla"]))}

    async def search(query, **kwargs):
        calls["queries"].append(query)
        return list(PAGES)

    async def structured_call(schema, system, user):
        calls["extract"].append(user)
        return calls["answer"]

    async def text_call(system, user, temperature=None):
        calls["recall"].append(user)
        return "En Carmen mencionan el Pulpo a la parrilla."

    monkeypatch.setattr(tavily, "is_enabled", lambda: True)
    monkeypatch.setattr(tavily, "search", search)
    monkeypatch.setattr(nodes, "structured_call", structured_call)
    monkeypatch.setattr(nodes, "text_call", text_call)
    monkeypatch.setattr(nodes.get_settings(), "enable_grounding_check", False)
    return calls


def named_lookup(message, name="Carmen", cards=()):
    ctx = UserContext(intent="lookup", language="Spanish", restaurant_name=name, location="Medellín")
    return {"context": ctx, "message": message, "last_cards": list(cards), "route": []}


async def test_find_dishes_reads_only_pages_about_the_restaurant(web):
    found = await dishes.find_dishes("Carmen", "Medellín")
    assert [d.name for d in found] == ["Pulpo a la parrilla"]
    assert web["queries"] == ['"Carmen" Medellín menu dishes what to order']
    assert "<untrusted_data" in web["extract"][0] and "El Cielo" not in web["extract"][0]


def test_pages_about_a_branch_in_another_city_are_skipped():
    dc = WebSnippet(title="El Cielo - Washington DC", url="https://www.kevineats.com/el-cielo-washington-dc",
                    content="El Cielo, born in Medellín, brings its tasting menu with an edible tree to DC.")
    medellin = WebSnippet(title="El Cielo", url="https://elcielorestaurant.com/medellin", content="Menú degustación")
    assert not ratings.about_place(dc, "El Cielo", "El Poblado, Medellín")
    assert ratings.about_place(medellin, "El Cielo", "El Poblado, Medellín")  # the city can be in the url
    assert ratings.about_place(dc, "El Cielo", None)  # unknown city: the name is all we can check
    assert not ratings.about_place(medellin, "Carmen", "Medellín")


async def test_injected_pages_never_reach_the_extractor(web, monkeypatch):
    monkeypatch.setattr(spotlight, "check_untrusted",
                        lambda text: GuardrailResult(allowed="Pulpo" not in text, stage="untrusted_content"))
    assert await dishes.find_dishes("Carmen", "Medellín") == []  # the only page naming the dish was dropped
    assert "Pulpo" not in web["extract"][0]


def test_named_places_outside_the_guide_are_researched_on_the_web(monkeypatch):
    monkeypatch.setattr(tavily, "is_enabled", lambda: True)
    assert nodes.route_intent(named_lookup("¿qué pido en Carmen?")) == "recall"
    assert nodes.route_intent(named_lookup("¿qué pido en Sakura Garden?", name="Sakura Garden")) == "tool_agent"
    assert nodes.route_intent(named_lookup("cuéntame más", name=None)) == "tool_agent"


async def test_dish_questions_list_the_verified_dishes_by_page(web):
    web["answer"] = extraction((0, ["Pulpo a la parrilla", "Cheesecake de maracuyá"]), (1, ["Risotto de hongos"]))
    out = await nodes.recall(named_lookup("¿qué platos me recomiendas de Carmen?"))
    assert out["answer"] == (
        "Estos son los platos de **Carmen** que mencionan en la web:\n"
        "- Pulpo a la parrilla, Cheesecake de maracuyá · [carmenmedellin.com](https://www.carmenmedellin.com/menu)\n"
        "- Risotto de hongos · [Tripadvisor](https://www.tripadvisor.com/carmen)"
    )
    assert out["route"] == ["recall", "recall:dishes:3"]
    assert out["evidence"][0] == "Dish named on carmenmedellin.com: Pulpo a la parrilla (https://www.carmenmedellin.com/menu)"
    assert web["recall"] == [] and len(web["queries"]) == 1  # fixed text: no free LLM answer, one search


async def test_dish_questions_say_when_no_dishes_were_found(web):
    web["answer"] = extraction((0, ["Ajiaco"]))  # not on the page: dropped
    out = await nodes.recall(named_lookup("¿qué platos me recomiendas de Carmen?"))
    assert out["answer"].startswith("No encontré en la web qué platos sirve Carmen")
    assert out["route"] == ["recall", "recall:dishes:0"] and "Ajiaco" not in out["answer"]


async def test_other_follow_ups_do_not_search_for_dishes(web):
    card = memory.CardRef(n=1, type="restaurant", id="web:abc", name="Carmen", location="Medellín")
    out = await nodes.recall(named_lookup("¿tiene terraza?", cards=[card]))
    assert len(web["queries"]) == 1 and "menu" not in web["queries"][0]
    assert out["route"] == ["recall", "recall:web"] and web["extract"] == []
