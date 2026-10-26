"""Profile extraction: cleanup of "null" values and recovery of answers that miss fields."""
import pytest
from langchain_core.messages import AIMessage

from connoisseur.agents import nodes
from connoisseur.schemas import ProfileExtraction, RecommendationSet

FULL = {"intent": "restaurant", "language": "Spanish", "location": "Medellín", "near_me": False,
        "cuisines": ["sushi"], "dietary": [], "vibe": None, "budget": None, "occasion": None,
        "restaurant_name": None, "search_query": "casual sushi"}


def test_null_words_become_empty_values():
    profile = ProfileExtraction(**{**FULL, "location": "null", "vibe": "None", "dietary": ["null", "vegan", ""],
                                   "cuisines": "Mexican", "search_query": "Mexican null"})
    assert profile.location is None and profile.vibe is None
    assert profile.dietary == ["vegan"]
    assert profile.cuisines == ["Mexican"]
    assert profile.search_query == "Mexican"


def test_partial_answer_keeps_what_the_model_said():
    partial = {k: v for k, v in FULL.items() if k not in ("budget", "restaurant_name")}
    profile = ProfileExtraction.from_partial(partial)
    assert profile is not None
    assert profile.location == "Medellín" and profile.cuisines == ["sushi"]
    assert profile.budget is None and profile.restaurant_name is None


def test_partial_answer_without_intent_is_a_restaurant_search():
    assert ProfileExtraction.from_partial({"location": "Medellín"}).intent == "restaurant"


def test_partial_answer_with_an_invalid_value_is_rejected():
    assert ProfileExtraction.from_partial({**FULL, "intent": "weather"}) is None


class FakeModel:
    """Stands in for get_chat_model(): returns what with_structured_output(include_raw=True) would."""

    def __init__(self, out):
        self.out = out

    def with_structured_output(self, schema, include_raw=False):
        assert include_raw
        return self

    async def ainvoke(self, messages):
        return self.out


def use_model(monkeypatch, out):
    monkeypatch.setattr(nodes, "get_chat_model", lambda **kw: FakeModel(out))


async def test_structured_call_recovers_a_tool_call_with_missing_fields(monkeypatch):
    # What Bedrock Nova did in production: budget and restaurant_name left out of the tool call
    args = {k: v for k, v in FULL.items() if k not in ("budget", "restaurant_name")}
    raw = AIMessage(content="", tool_calls=[{"name": "ProfileExtraction", "args": args, "id": "1"}])
    use_model(monkeypatch, {"raw": raw, "parsed": None, "parsing_error": ValueError("2 validation errors")})
    profile = await nodes.structured_call(ProfileExtraction, "system", "user")
    assert profile is not None and profile.location == "Medellín"


async def test_structured_call_recovers_a_json_body(monkeypatch):
    raw = AIMessage(content='{"intent": "recipe", "language": "English", "cuisines": ["Thai"]}')
    use_model(monkeypatch, {"raw": raw, "parsed": None, "parsing_error": ValueError("missing")})
    profile = await nodes.structured_call(ProfileExtraction, "system", "user")
    assert profile.intent == "recipe" and profile.cuisines == ["Thai"]


async def test_structured_call_returns_the_parsed_answer(monkeypatch):
    use_model(monkeypatch, {"raw": AIMessage(content=""), "parsed": ProfileExtraction(**FULL), "parsing_error": None})
    assert (await nodes.structured_call(ProfileExtraction, "system", "user")).search_query == "casual sushi"


@pytest.mark.parametrize("raw", [AIMessage(content="not json"), None])
async def test_structured_call_without_recovery_returns_none(monkeypatch, raw):
    use_model(monkeypatch, {"raw": raw, "parsed": None, "parsing_error": ValueError("bad")})
    assert await nodes.structured_call(ProfileExtraction, "system", "user") is None


async def test_schemas_without_from_partial_are_not_completed(monkeypatch):
    raw = AIMessage(content='{"summary": "Hi"}')  # restaurants and recipes missing: must not validate
    use_model(monkeypatch, {"raw": raw, "parsed": None, "parsing_error": ValueError("missing")})
    assert await nodes.structured_call(RecommendationSet, "system", "user") is None
