"""The hybrid orchestrator: it picks tools, the tools write the answer, and the fixed routing answers when
it fails. LLM, retrieval and OSM are the fakes of test_graph.py; the orchestrator's model is scripted."""
import pytest
from langchain_core.messages import AIMessage, ToolMessage
from tests import test_graph
from tests.test_graph import ScriptedModel, profile, resolved, run

from connoisseur.agents import orchestrator as orch
from connoisseur.agents import skills
from connoisseur.agents.runner import stream_chat
from connoisseur.settings import get_settings

fakes = test_graph.fakes  # the fixture of test_graph.py: fake LLM, retrieval and OSM


def call(name, args, id_="c1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": id_}])


class RecordingModel(ScriptedModel):
    """Also keeps the messages of each call, to check what the orchestrator was shown."""

    def __init__(self, replies):
        super().__init__(replies)
        self.seen = []

    async def ainvoke(self, messages):
        self.seen.append(list(messages))
        if not self.replies:
            raise AssertionError("the orchestrator called the model after the turn had ended")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def scripted(monkeypatch):
    monkeypatch.setattr(get_settings(), "orchestrator_enabled", True)

    def use(*replies):
        model = RecordingModel(replies)
        monkeypatch.setattr(orch, "get_chat_model", lambda temperature=None: model)
        return model

    return use


MEDELLIN_SEARCH = {"what": "vegan", "where": "El Poblado, Medellín"}


async def test_one_tool_answers_with_the_same_branch_as_the_fixed_graph(fakes, scripted):
    fakes["profile"] = profile("restaurant", location="El Poblado, Medellín")
    fakes["geocode"] = resolved("Antioquia")
    model = scripted(AIMessage(content="All done!", tool_calls=[
        {"name": "search_restaurants", "args": MEDELLIN_SEARCH, "id": "c1"}]))
    out = await run("vegan food in El Poblado")
    assert out["route"][:4] == ["intent:restaurant", "orch", "orch:search_restaurants", "location:elsewhere"]
    assert "web:osm" in out["route"] and "Green Bowl" in out["answer"]
    assert "All done!" not in out["answer"]  # the orchestrator's own text is discarded
    assert len(model.seen) == 1  # plan, then execute: no second call just to hear it is done
    assert set(model.tools) == {"search_restaurants", "place_info", "search_recipes", "recipe_steps", "ask_user",
                                "answer_canned"}


async def test_an_error_gets_one_more_round_and_the_model_sees_only_tagged_summaries(fakes, scripted):
    fakes["profile"] = profile("restaurant", location="El Poblado, Medellín")
    fakes["geocode"] = resolved("Antioquia")
    model = scripted(AIMessage(content="", tool_calls=[
        {"name": "search_restaurants", "args": MEDELLIN_SEARCH, "id": "c1"},
        {"name": "ask_user", "args": {"kind": "your password"}, "id": "c2"}]),  # invalid: retried
        AIMessage(content=""))
    out = await run("vegan food in El Poblado")
    assert len(model.seen) == 2 and "orch:bad_args" in out["route"] and "Green Bowl" in out["answer"]
    summary = next(m for m in model.seen[1] if isinstance(m, ToolMessage)).content
    assert summary.startswith("<untrusted_data") and '"restaurants": ["Green Bowl"]' in summary
    assert "reasoning" not in summary and "description" not in summary


async def test_two_tools_in_one_turn_and_the_second_sees_the_first_ones_picks(fakes, scripted):
    fakes["profile"] = profile("restaurant", location="El Poblado, Medellín")
    fakes["geocode"] = resolved("Antioquia")
    scripted(AIMessage(content="", tool_calls=[
        {"name": "search_restaurants", "args": MEDELLIN_SEARCH, "id": "c1"},
        {"name": "place_info", "args": {"place": "the first one", "question": "which dishes to order"}, "id": "c2"}]))
    out = await run("a vegan place in El Poblado and what to order there")
    assert "orch:search_restaurants" in out["route"] and "orch:place_info" in out["route"]
    assert out["route"].index("orch:place_info") > out["route"].index("orch:search_restaurants")
    # The dish lookup resolved "the first one" to Green Bowl, found in this same turn
    assert "Green Bowl" in out["answer"].split("\n\n")[-1]


async def test_a_compound_request_planned_one_call_at_a_time_gets_a_second_round(fakes, scripted):
    fakes["profile"] = profile("restaurant", location="El Poblado, Medellín")
    fakes["geocode"] = resolved("Antioquia")
    model = scripted(call("search_restaurants", MEDELLIN_SEARCH),
                     call("place_info", {"place": "the first one", "question": "which dishes"}, "c2"))
    out = await run("Find a vegan place in El Poblado and tell me what to order at the first one")
    assert len(model.seen) == 2 and "orch:place_info" in out["route"]


async def test_positions_are_resolved_by_code_not_by_the_model(fakes, scripted):
    from connoisseur import memory

    cards = [memory.CardRef(n=1, type="restaurant", id="web:1", name="Sushi World", location="Medellín"),
             memory.CardRef(n=2, type="restaurant", id="web:2", name="Sushi Light", location="Medellín")]
    state = {"context": profile("lookup", language="Spanish").to_context(), "message": "¿Qué pido en el segundo?",
             "last_cards": cards, "route": [], "history": []}
    # The model named the wrong place for "el segundo"
    out = await skills.run(state, "place_info", {"place": "Sushi World", "question": "what to order"})
    assert "Sushi Light" in out.answer and "Sushi World" not in out.answer
    # A name the user wrote is kept even if the message also has a position
    state["message"] = "¿Sushi World es la primera opción? ¿qué pido allí?"
    out = await skills.run(state, "place_info", {"place": "Sushi World", "question": "what to order"})
    assert "Sushi World" in out.answer


@pytest.mark.parametrize(("message", "several"), [
    ("Find me a moody Italian spot in Pasadena and tell me what to order at the first one", True),
    ("Busca un italiano en El Poblado y dime qué pedir", True),
    ("Dame una receta de pasta y también un restaurante de sushi", True),
    ("Busco un restaurante italiano y barato en El Poblado", False),
    ("Soy vegano y alérgico al maní", False),
])
def test_requests_for_several_things(message, several):
    assert orch._asks_several(message) is several


async def test_asking_the_user_ends_the_turn(fakes, scripted):
    fakes["profile"] = profile("restaurant", near_me=True, language="Spanish")
    scripted(AIMessage(content="", tool_calls=[
        {"name": "ask_user", "args": {"kind": "location"}, "id": "c1"},
        {"name": "search_restaurants", "args": {"what": "sushi"}, "id": "c2"}]))  # skipped after the question
    out = await run("sushi cerca de mí")
    assert out["answer"].startswith("¡Con gusto busco lugares cerca de ti!")
    assert "orch:search_restaurants" not in out["route"] and "ask:location" in out["route"]


@pytest.mark.parametrize("replies", [
    [call("delete_everything", {}), AIMessage(content="")],  # unknown tool
    [call("search_restaurants", {"what": ""}), AIMessage(content="")],  # invalid arguments
    [AIMessage(content="I think you'd like sushi.")],  # no tool at all
    [RuntimeError("Bedrock is down")],
])
async def test_the_fixed_routing_answers_when_the_orchestrator_cannot(fakes, scripted, replies):
    fakes["profile"] = profile("restaurant", location="El Poblado, Medellín")
    fakes["geocode"] = resolved("Antioquia")
    scripted(*replies)
    out = await run("vegan food in El Poblado")
    assert "orch:fallback" in out["route"] and "Green Bowl" in out["answer"]
    assert "you'd like sushi" not in out["answer"]


async def test_repeated_calls_are_skipped_and_the_loop_ends(fakes, scripted, monkeypatch):
    fakes["profile"] = profile("recipe", language="Spanish")
    monkeypatch.setattr(get_settings(), "orchestrator_max_steps", 3)
    same = {"what": "noodles"}
    scripted(*[call("search_recipes", same, f"c{i}") for i in range(3)])
    out = await run("una receta de noodles")
    assert out["route"].count("orch:search_recipes") == 1 and "Peanut Noodles" in out["answer"]


async def test_steps_are_not_fetched_again_after_a_full_recipe(fakes, scripted, monkeypatch):
    from connoisseur.agents import nodes

    fakes["profile"] = profile("recipe", language="Spanish", dish="Pasta Arrabbiata")
    scripted(AIMessage(content="", tool_calls=[
        {"name": "search_recipes", "args": {"what": "pasta arrabbiata", "dish": "Pasta Arrabbiata"}, "id": "c1"},
        {"name": "recipe_steps", "args": {"recipe": "Pasta Arrabbiata"}, "id": "c2"}]), AIMessage(content=""))
    monkeypatch.setattr(nodes.tavily, "is_enabled", lambda: False)
    out = await run("¿cómo se hace la pasta arrabbiata?")
    assert out["route"].count("recipe_detail") == 1 and "orch:recipe_steps" not in out["route"]
    assert out["answer"].count("**Pasta Arrabbiata**") == 1


async def test_a_plain_recipe_list_does_not_also_get_steps(fakes, scripted):
    fakes["profile"] = profile("recipe", language="Spanish")
    scripted(AIMessage(content="", tool_calls=[
        {"name": "search_recipes", "args": {"what": "noodles"}, "id": "c1"},
        {"name": "recipe_steps", "args": {"recipe": "the first one"}, "id": "c2"}]), AIMessage(content=""))
    out = await run("Dame una receta de noodles")
    assert "orch:recipe_steps" not in out["route"] and "Peanut Noodles" in out["answer"]


async def test_greetings_skip_the_orchestrator(fakes, scripted):
    fakes["profile"] = profile("clarification", language="Spanish")
    scripted()  # any call to the orchestrator's model would fail the test
    out = await run("hola")
    assert out["route"] == ["intent:clarification", "canned:help"]


async def test_tool_progress_reaches_the_stream(fakes, scripted):
    fakes["profile"] = profile("restaurant", location="El Poblado, Medellín")
    fakes["geocode"] = resolved("Antioquia")
    scripted(call("search_restaurants", MEDELLIN_SEARCH), AIMessage(content=""))
    steps = [e["label"] for e in [e async for e in stream_chat("vegan food in El Poblado")] if e["type"] == "step"]
    assert "Searching restaurants" in steps
    assert steps.index("Searching restaurants") < steps.index("Checking the answer")


def test_compose_keeps_shared_notes_once():
    note = "⚠️ _Nota sobre alergias: ..._"
    first = skills.SkillResult(answer=f"**Restaurantes**\n1. A\n\n{note}", cards=[{"type": "restaurant"}])
    second = skills.SkillResult(answer=f"**Recetas**\n1. B\n\n{note}", cards=[{"type": "recipe"}])
    out = orch.compose({"route": ["intent:both"]}, [("search_restaurants", first), ("search_recipes", second)])
    assert out["answer"].count(note) == 1 and len(out["cards"]) == 2
    assert out["route"] == ["intent:both", "orch", "orch:search_restaurants", "orch:search_recipes"]


async def test_unknown_tools_and_bad_arguments_never_raise():
    state = {"context": profile("restaurant").to_context(), "message": "x", "route": []}
    assert (await skills.run(state, "rm_rf", {})).summary["status"] == "error"
    assert (await skills.run(state, "ask_user", {"kind": "your password"})).summary["status"] == "error"
