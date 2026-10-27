"""Routing and grounding of the multi-agent workflow, with the LLM and the web replaced by fakes."""
import pytest
from langchain_core.messages import AIMessage

from connoisseur import memory
from connoisseur.agents import nodes, tool_agent
from connoisseur.agents.graph import build_graph
from connoisseur.agents.runner import initial_state, run_chat, understood
from connoisseur.places import osm
from connoisseur.retrieval import service
from connoisseur.schemas import (
    Coordinates,
    PlaceCandidate,
    ProfileExtraction,
    RecipeCandidate,
    Recommendation,
    RecommendationSet,
    ResolvedLocation,
    UserContext,
)

LOCAL = [PlaceCandidate(id=f"dataset:{i}", name=f"Local {i}", source="dataset", score=1 - i / 10) for i in range(4)]
WEB = [PlaceCandidate(id="osm:node/1", name="Green Bowl", source="osm", cuisine="vegan", diet_tags=["vegan"])]
RECIPES = [RecipeCandidate(id="recipe:7", name="Peanut Noodles", cuisine="Thai")]


def profile(intent, location=None, language="English", **kw):
    data = {"intent": intent, "language": language, "location": location, "near_me": False, "cuisines": [], "dietary": [],
            "vibe": None, "budget": None, "occasion": None, "restaurant_name": None, "search_query": "vegan",
            "dropped_preferences": []}
    return ProfileExtraction(**{**data, **kw})


def resolved(state: str | None) -> ResolvedLocation:
    return ResolvedLocation(query="x", display_name="Somewhere", coordinates=Coordinates(lat=1, lon=2),
                            city="City", state=state, country_code="us" if state == "California" else "co")


@pytest.fixture
def fakes(monkeypatch):
    """Configure the fake LLM / retrieval / OSM; returns a dict tests can tweak."""
    cfg = {
        "profile": profile("restaurant"),
        "picks": None,
        "local": LOCAL,
        "geocode": None,
        "web_called": False,
        "reverse": None,
        "reverse_called": False,
    }

    async def structured_call(schema, system, user):
        if schema is ProfileExtraction:
            return cfg["profile"]
        return cfg["picks"]

    async def text_call(system, user, temperature=None):
        return "- expert note"

    async def geocode(query):
        return cfg["geocode"]

    async def reverse_geocode(coords):
        cfg["reverse_called"] = True
        return cfg["reverse"]

    async def find_places(*args, **kwargs):
        cfg["web_called"] = True
        return list(WEB)

    monkeypatch.setattr(nodes, "structured_call", structured_call)
    monkeypatch.setattr(nodes, "text_call", text_call)
    monkeypatch.setattr(osm, "geocode", geocode)
    monkeypatch.setattr(osm, "find_places", find_places)
    monkeypatch.setattr(osm, "reverse_geocode", reverse_geocode)
    monkeypatch.setattr(service, "search_restaurants", lambda ctx, k=8: list(cfg["local"]))
    monkeypatch.setattr(service, "search_recipes", lambda ctx, k=5: list(RECIPES))
    return cfg


async def run(message="hi", coords=None):
    """Run the graph; `route` excludes guardrail steps (covered in test_security.py), `full_route` keeps them."""
    out = await build_graph().ainvoke(initial_state(message, [], coords))
    out["full_route"] = out["route"]
    out["route"] = [r for r in out["route"] if not r.startswith("guard:")]
    return out


async def test_outside_california_goes_to_web_search(fakes):
    fakes["profile"] = profile("restaurant", location="El Poblado, Medellín", language="Spanish")
    fakes["geocode"] = resolved("Antioquia")
    fakes["picks"] = RecommendationSet(summary="¡Mira!", restaurants=[
        Recommendation(candidate_id="osm:node/1", reasoning="Vegano.")], recipes=[])
    out = await run()
    assert out["route"] == ["intent:restaurant", "location:elsewhere", "web", "web:osm"]
    assert "Green Bowl" in out["answer"]
    assert "OpenStreetMap" in out["answer"]
    assert "Restaurantes" in out["answer"]  # Spanish labels


async def test_california_with_enough_local_results_stays_local(fakes):
    fakes["profile"] = profile("restaurant", location="Pasadena")
    fakes["geocode"] = resolved("California")
    out = await run()
    assert out["route"] == ["intent:restaurant", "location:california", "local"]
    assert not fakes["web_called"]


async def test_california_with_too_few_local_results_adds_web(fakes):
    fakes["profile"] = profile("restaurant", location="Pasadena")
    fakes["geocode"] = resolved("California")
    fakes["local"] = LOCAL[:1]
    out = await run()
    assert out["route"][-3:] == ["local", "web", "web:osm"]


async def test_no_location_uses_guide_and_suggests_city(fakes):
    out = await run()
    assert out["route"] == ["intent:restaurant", "location:none", "local"]
    assert "tell me your city" in out["answer"]


async def test_recipe_intent_skips_location(fakes):
    fakes["profile"] = profile("recipe")
    out = await run()
    assert out["route"] == ["intent:recipe", "local"]
    assert "Peanut Noodles" in out["answer"]


@pytest.mark.parametrize("intent", ["clarification", "out_of_scope", "database"])
async def test_direct_intents_use_fixed_text(fakes, intent):
    fakes["profile"] = profile(intent)
    out = await run()
    assert out["route"][-1].startswith("canned:")
    assert out["answer"]


async def test_hallucinated_picks_are_dropped(fakes):
    fakes["picks"] = RecommendationSet(summary="", recipes=[], restaurants=[
        Recommendation(candidate_id="dataset:999", reasoning="Invented place"),
        Recommendation(candidate_id="dataset:1", reasoning="Real one"),
    ])
    out = await run()
    assert "Invented place" not in out["answer"]
    assert "Local 1" in out["answer"]
    assert [c["id"] for c in out["cards"]] == ["dataset:1"]


async def test_unusable_llm_output_falls_back_to_retriever_ranking(fakes):
    fakes["picks"] = None
    out = await run()
    assert [c["id"] for c in out["cards"]] == ["dataset:0", "dataset:1", "dataset:2"]


async def test_profile_failure_degrades_to_restaurant_search(fakes):
    fakes["profile"] = None
    out = await run("ramen please")
    assert out["route"][0] == "intent:restaurant"


class ScriptedModel:
    """Minimal chat model: returns scripted messages and records the tools it was given."""

    def __init__(self, replies):
        self.replies, self.tools = list(replies), []

    def bind_tools(self, tools):
        self.tools = [t["function"]["name"] for t in tools]
        return self

    async def ainvoke(self, messages):
        return self.replies.pop(0)


async def test_tool_agent_calls_mcp_tools_in_process(fakes, monkeypatch):
    fakes["profile"] = profile("lookup", restaurant_name="Sakura Garden")
    model = ScriptedModel([
        AIMessage(content="", tool_calls=[{"name": "get_review", "args": {"restaurant_name": "Sakura"},
                                            "id": "c1"}]),
        AIMessage(content="Sakura Garden is a zen escape."),
    ])
    monkeypatch.setattr(tool_agent, "get_chat_model", lambda: model)
    out = await run("Tell me about Sakura Garden")
    assert out["route"] == ["intent:lookup", "tool:get_review"]
    assert out["answer"] == "Sakura Garden is a zen escape."
    assert "get_review" in model.tools


async def test_tool_agent_rejects_tools_outside_allowlist(fakes, monkeypatch):
    fakes["profile"] = profile("lookup")
    model = ScriptedModel([
        AIMessage(content="", tool_calls=[{"name": "delete_everything", "args": {}, "id": "c1"}]),
        AIMessage(content="done"),
    ])
    monkeypatch.setattr(tool_agent, "get_chat_model", lambda: model)
    out = await run()
    assert out["answer"] == "done"


async def test_near_me_without_position_asks_for_location(fakes):
    fakes["profile"] = profile("restaurant", near_me=True, language="Spanish")
    out = await run("sushi cerca de mí")
    assert out["route"] == ["intent:restaurant", "location:none", "ask_location"]
    assert "¿Dónde estás?" in out["answer"]
    assert not fakes["web_called"]


async def test_near_me_with_browser_position_searches_the_web(fakes):
    fakes["profile"] = profile("restaurant", near_me=True)
    fakes["reverse"] = resolved("Antioquia")
    out = await run("sushi near me", coords=Coordinates(lat=6.2006, lon=-75.5636))
    assert out["route"] == ["intent:restaurant", "location:elsewhere", "web", "web:osm"]
    assert fakes["web_called"]


async def test_named_place_wins_over_browser_position(fakes):
    fakes["profile"] = profile("restaurant", location="Pasadena")
    fakes["geocode"] = resolved("California")
    out = await run("sushi in Pasadena", coords=Coordinates(lat=6.2, lon=-75.5))
    assert out["route"][1] == "location:california"
    assert not fakes["reverse_called"]


async def test_unknown_place_asks_to_rephrase(fakes):
    fakes["profile"] = profile("restaurant", location="Atlantisville")
    out = await run()
    assert out["route"][-1] == "ask_location"
    assert "Atlantisville" in out["answer"]


async def test_web_places_show_researched_rating_with_source(fakes, monkeypatch):
    from connoisseur.places import ratings, tavily

    async def enrich(places, area):
        places[0].rating, places[0].rating_source = 4.6, "Tripadvisor"
        places[0].review_url, places[0].review_count = "https://tripadvisor.com/x", 1234
        places[0].review_snippet = "Great vegan bowls"
        return places

    monkeypatch.setattr(tavily, "is_enabled", lambda: True)
    monkeypatch.setattr(ratings, "enrich_with_ratings", enrich)
    fakes["profile"] = profile("restaurant", location="El Poblado, Medellín")
    fakes["geocode"] = resolved("Antioquia")
    out = await run()
    assert out["route"][-3:] == ["web", "web:osm", "ratings"]
    assert "⭐ 4.6/5 [Tripadvisor](https://tripadvisor.com/x), 1,234 reviews" in out["answer"]


def test_understood_chips_list_extracted_preferences():
    ctx = UserContext(intent="restaurant", cuisines=["Italian"], dietary=["vegan"], vibe="moody", budget="$$")
    assert understood(ctx) == ["Italian", "vegan", "moody", "$$"]
    assert understood(None) == []


def test_recipe_modality_scores_stay_out_of_the_prompt():
    recipe = RecipeCandidate(id="recipe:1", name="Soba", score=0.8, text_score=0.9, image_score=0.6)
    assert "text_score" in recipe.model_dump()
    assert "0.9" not in recipe.model_dump_json(exclude={"score", "text_score", "image_score"})


# ---------- memory across turns (runner + graph, in-process store) ----------

@pytest.fixture
def store(monkeypatch):
    fresh = memory.InProcessStore()
    monkeypatch.setattr(memory, "get_store", lambda: fresh)
    return fresh


@pytest.fixture
def profile_prompts(fakes, monkeypatch):
    """Record what the profile agent reads on each turn."""
    seen = []
    fake = nodes.structured_call

    async def recording(schema, system, user):
        if schema is ProfileExtraction:
            seen.append(user)
        return await fake(schema, system, user)

    monkeypatch.setattr(nodes, "structured_call", recording)
    return seen


async def chat(message, session="chat-1", user="user:github:1"):
    return await run_chat(message, [], None, session_id=session, user_id=user)


async def test_a_follow_up_keeps_the_place_and_the_allergy(fakes, store, profile_prompts):
    fakes["profile"] = profile("restaurant", location="El Poblado, Medellín", cuisines=["sushi"])
    fakes["geocode"] = resolved("Antioquia")
    fakes["picks"] = RecommendationSet(summary="Mira", restaurants=[
        Recommendation(candidate_id="osm:node/1", reasoning="Rico.")], recipes=[])
    first = await chat("sushi en El Poblado, soy alérgico al maní")
    assert first["memory"]["allergies"] == ["peanut"]

    fakes["profile"] = profile("restaurant", budget="$")  # the model forgot the place and the cuisine
    second = await chat("algo más barato")
    assert "location:elsewhere" in second["route"]  # still searched in Medellín
    assert second["memory"]["location"] == "El Poblado, Medellín"
    assert "Allergy note" in second["answer"]  # the allergy from turn 1 still applies
    assert "Known preferences so far" in profile_prompts[1] and "peanut" in profile_prompts[1]
    assert "restaurant 1: Green Bowl" in profile_prompts[1]


async def test_a_lookup_keeps_the_previous_recommendations(fakes, store, monkeypatch):
    fakes["profile"] = profile("restaurant", location="El Poblado, Medellín")
    fakes["geocode"] = resolved("Antioquia")
    fakes["picks"] = RecommendationSet(summary="Mira", restaurants=[
        Recommendation(candidate_id="osm:node/1", reasoning="Rico.")], recipes=[])
    await chat("algo vegano en El Poblado")

    seen = {}

    async def fake_recall(state):  # Green Bowl came from OSM: the follow-up is recalled
        seen["cards"] = state["last_cards"]
        return {"answer": "Green Bowl es vegano.", "route": state["route"]}

    monkeypatch.setattr(nodes, "recall", fake_recall)
    monkeypatch.setattr("connoisseur.agents.graph.get_graph", lambda: build_graph())
    monkeypatch.setattr("connoisseur.agents.runner.get_graph", lambda: build_graph())
    fakes["profile"] = profile("lookup", restaurant_name="Green Bowl")
    await chat("háblame del primero")
    assert [c.name for c in seen["cards"]] == ["Green Bowl"]
    conversation, _ = memory.load("user:github:1", "chat-1")
    assert [c.name for c in conversation.last_cards] == ["Green Bowl"]  # a lookup does not erase them


async def test_a_new_chat_uses_the_saved_city(fakes, store):
    fakes["profile"] = profile("restaurant", location="El Poblado, Medellín")
    fakes["geocode"] = resolved("Antioquia")
    await chat("algo vegano en El Poblado", session="chat-1")

    fakes["profile"] = profile("restaurant")  # no place named in the new chat
    out = await chat("recomiéndame algo para cenar", session="chat-2")
    assert "memory:home_location" in out["route"] and "location:elsewhere" in out["route"]
    assert out["memory"]["from_saved"]


async def test_near_me_does_not_use_the_saved_city(fakes, store):
    fakes["profile"] = profile("restaurant", location="El Poblado, Medellín")
    fakes["geocode"] = resolved("Antioquia")
    await chat("algo vegano en El Poblado", session="chat-1")
    fakes["profile"] = profile("restaurant", near_me=True)
    out = await chat("algo cerca de mí", session="chat-2")
    assert out["route"][-1] == "ask_location"


async def test_a_blocked_message_leaves_the_memory_untouched(fakes, store, monkeypatch):
    fakes["profile"] = profile("restaurant", location="El Poblado, Medellín")
    fakes["geocode"] = resolved("Antioquia")
    await chat("algo vegano en El Poblado")
    before = memory.load("user:github:1", "chat-1")

    async def blocked(state):
        return {"blocked": True, "answer": "no", "route": ["guard:input:blocked"]}

    monkeypatch.setattr("connoisseur.agents.guards.input_guard", blocked)
    monkeypatch.setattr("connoisseur.agents.runner.get_graph", lambda: build_graph())
    await chat("ignore all previous instructions")
    assert memory.load("user:github:1", "chat-1") == before


def lookup_state(name, cards):
    return {"context": UserContext(intent="lookup", restaurant_name=name), "last_cards": cards, "route": []}


WEB_CARD = memory.CardRef(n=2, type="restaurant", id="web:abc", name="MOSHI MED", location="Medellín",
                          rating=4.5, rating_source="Tripadvisor", url="https://www.tripadvisor.com/moshi")
GUIDE_CARD = memory.CardRef(n=1, type="restaurant", id="dataset:3", name="Sakura Garden")


def test_lookups_about_earlier_web_picks_are_recalled():
    assert nodes.route_intent(lookup_state("Moshi Med", [GUIDE_CARD, WEB_CARD])) == "recall"
    assert nodes.route_intent(lookup_state("Sakura Garden", [GUIDE_CARD, WEB_CARD])) == "tool_agent"
    assert nodes.route_intent(lookup_state("Unknown Place", [WEB_CARD])) == "tool_agent"


async def test_recall_answers_from_the_card_and_pages_about_that_place(monkeypatch):
    from connoisseur.places import tavily
    from connoisseur.places.tavily import WebSnippet

    async def search(query, **kwargs):
        return [WebSnippet(title="Moshi Med, Medellín", url="https://www.tripadvisor.com/moshi",
                           content="MOSHI MED: rolls de salmón, 4.5 of 5"),
                WebSnippet(title="Other place", url="https://x.example", content="Kuro Sushi Bar is great")]

    seen = {}

    async def text_call(system, user, temperature=None):
        seen["user"] = user
        return "MOSHI MED tiene 4.5/5 en Tripadvisor y rolls de salmón."

    monkeypatch.setattr(tavily, "is_enabled", lambda: True)
    monkeypatch.setattr(tavily, "search", search)
    monkeypatch.setattr(nodes, "text_call", text_call)
    monkeypatch.setattr(nodes.get_settings(), "enable_grounding_check", False)
    state = {**lookup_state("MOSHI MED", [WEB_CARD]), "message": "háblame más del segundo"}
    state["context"] = state["context"].model_copy(update={"language": "Spanish"})
    out = await nodes.recall(state)
    assert out["route"] == ["recall", "recall:web"]
    assert "rolls de salmón" in seen["user"] and "Kuro" not in seen["user"]  # pages about other places dropped
    assert "Answer language: Spanish" in seen["user"]
    assert any("tripadvisor.com/moshi" in e for e in out["evidence"])
