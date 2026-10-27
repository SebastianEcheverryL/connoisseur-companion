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
    "What is good to eat there?", "¿Qué tal es la comida del tercero?",
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
    assert nodes.route_intent(named_lookup("cuéntame de Carmen")) == "recall"
    # Guide places: the tool agent for general questions, recall (guide signatures) for dishes
    assert nodes.route_intent(named_lookup("¿tiene buenas reseñas?", name="Sakura Garden")) == "tool_agent"
    assert nodes.route_intent(named_lookup("¿qué pido en Sakura Garden?", name="Sakura Garden")) == "recall"
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


CARDS = [memory.CardRef(n=1, type="restaurant", id="web:a", name="Della Nonna", location="Medellín"),
         memory.CardRef(n=2, type="restaurant", id="web:b", name="POMO D'ORO", location="Medellín"),
         memory.CardRef(n=1, type="recipe", id="recipe:1", name="Lasagna"),
         memory.CardRef(n=3, type="restaurant", id="dataset:1000001", name="The Gilded Artichoke")]


@pytest.mark.parametrize(("text", "position"), [
    ("¿Qué platos me recomiendas del primero?", 1), ("the second one", 2), ("y en la tercera", 3),
    ("the 2nd", 2), ("el número 3", 3), ("#2", 2), ("el último", -1), ("Della Nonna", None),
])
def test_positions_in_follow_ups(text, position):
    assert nodes._ordinal(text) == position


@pytest.mark.parametrize(("name", "pointer"), [
    ("the first one", True), ("el segundo", True), ("ese lugar", True), ("that place", True),
    ("Della Nonna", False), ("Restaurante Romero", False), ("El Cielo", False), (None, False),
])
def test_names_that_only_point_back(name, pointer):
    assert nodes._is_reference(name) is pointer


@pytest.mark.parametrize(("name", "message", "expected"), [
    ("the first one", "¿Qué platos me recomiendas del primero?", "Della Nonna"),  # Nova copies the pointer
    (None, "what should I order at the second one?", "POMO D'ORO"),
    ("el último", "¿y del último?", "The Gilded Artichoke"),
    ("pomo d'oro", "¿qué pido allí?", "POMO D'ORO"),
    ("Carmen", "¿Carmen es la primera opción?", None),  # a real name wins over a position in the text
])
def test_follow_ups_find_the_card_by_name_or_position(name, message, expected):
    state = {"context": UserContext(intent="lookup", restaurant_name=name), "message": message, "last_cards": CARDS}
    card = nodes._previous_card(state)
    assert (card.name if card else None) == expected


def test_there_means_the_place_the_last_answer_talked_about():
    history = [{"role": "user", "content": "What dishes do you recommend at the second one?"},
               {"role": "assistant", "content": "These are the dishes of **POMO D'ORO** mentioned on the web: ..."}]
    state = {"context": UserContext(intent="lookup"), "message": "What is good to eat there?", "last_cards": CARDS,
             "history": history}
    assert nodes._previous_card(state).name == "POMO D'ORO"
    state["message"] = "What is good to eat?"  # no pointer: not enough to pick one of several places
    assert nodes._previous_card(state) is None


def test_placeholders_are_not_restaurant_names():
    from connoisseur.schemas import PROFILE_DEFAULTS, ProfileExtraction

    for slot in ("<<Previous recommendations[0].name>>", "[restaurant name]", "{name}"):
        extracted = ProfileExtraction.model_validate({**PROFILE_DEFAULTS, "restaurant_name": slot})
        assert extracted.restaurant_name is None


async def test_a_pointer_copied_from_an_earlier_turn_is_dropped(monkeypatch):
    from connoisseur.schemas import PROFILE_DEFAULTS, ProfileExtraction

    async def structured_call(schema, system, user):  # what Nova returned after "¿De cuál restaurante...?"
        return ProfileExtraction.model_validate({**PROFILE_DEFAULTS, "intent": "lookup", "language": "Spanish",
                                                 "location": "Laureles, Medellín", "cuisines": ["sushi"],
                                                 "restaurant_name": "the first one"})

    monkeypatch.setattr(nodes, "structured_call", structured_call)
    out = await nodes.profile({"message": "Busco sushi en Laureles, Medellín", "history": [], "route": []})
    assert out["context"].intent == "restaurant" and out["context"].restaurant_name is None
    out = await nodes.profile({"message": "¿Qué pido en el primero?", "history": [], "route": []})
    assert out["context"].intent == "lookup"  # here the message itself points back
    # Location and cuisine copied from earlier turns do not make a follow-up question a new search
    out = await nodes.profile({"message": "¿tiene terraza?", "history": [], "route": []})
    assert out["context"].intent == "lookup"


async def test_a_search_read_as_a_lookup_without_a_name_is_a_search(monkeypatch):
    from connoisseur.schemas import PROFILE_DEFAULTS, ProfileExtraction

    async def structured_call(schema, system, user):
        return ProfileExtraction.model_validate({**PROFILE_DEFAULTS, "intent": "lookup", "language": "Spanish",
                                                 "location": "El Poblado, Medellín", "cuisines": ["Italian"]})

    monkeypatch.setattr(nodes, "structured_call", structured_call)
    out = await nodes.profile({"message": "Busco restaurantes italianos en El Poblado, Medellín", "history": [],
                               "route": []})
    assert out["context"].intent == "restaurant"


def test_there_means_the_only_restaurant_shown():
    state = {"context": UserContext(intent="lookup", restaurant_name="ese lugar"), "message": "¿qué pido ahí?",
             "last_cards": CARDS[:1]}
    assert nodes._previous_card(state).name == "Della Nonna"


async def test_pointing_back_with_nothing_shown_asks_which_place(web):
    state = named_lookup("¿Qué platos me recomiendas del primero?", name="the first one")
    assert nodes.route_intent(state) == "recall"
    out = await nodes.recall(state)
    assert out["answer"].startswith("¿De cuál restaurante me hablas?")
    assert out["route"] == ["recall", "recall:no_reference"] and web["queries"] == []


async def test_dishes_of_guide_places_come_from_the_guide(web):
    # An earlier guide pick goes to recall (not the tool agent, which once answered without calling a tool)
    state = named_lookup("What dishes do you recommend at the third one?", name="the third one", cards=CARDS)
    assert nodes.route_intent(state) == "recall"
    out = await nodes.recall(state)
    assert out["answer"] == ("Estos son los platos insignia de **The Gilded Artichoke** según la Guía Connoisseur:\n"
                             "- lavender-rubbed roasted chicken\n- delicate heirloom tomato tarts")
    assert out["route"] == ["recall", "recall:guide_dishes:2"] and web["queries"] == []
    named = await nodes.recall(named_lookup("¿qué pido en The Gilded Artichoke?", name="the gilded artichoke"))
    assert named["route"] == ["recall", "recall:guide_dishes:2"]


async def test_other_follow_ups_do_not_search_for_dishes(web):
    card = memory.CardRef(n=1, type="restaurant", id="web:abc", name="Carmen", location="Medellín")
    out = await nodes.recall(named_lookup("¿tiene terraza?", cards=[card]))
    assert len(web["queries"]) == 1 and "menu" not in web["queries"][0]
    assert out["route"] == ["recall", "recall:web"] and web["extract"] == []
