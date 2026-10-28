"""High-level tools the orchestrator can call. Each one runs the same nodes as the fixed graph, in the same
order, so it stays deterministic and grounded inside (verified web results, allergen filters, fixed texts),
keeps the same route markers, and returns:

- a section of the final answer and its cards, written by code or by the grounded recommender;
- a short summary for the orchestrator (counts and names), never page text or reviews: web content
  must not be able to steer which tool runs next.
"""
import asyncio
import json
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from connoisseur.agents import nodes
from connoisseur.agents.state import ChatState
from connoisseur.agents.tool_agent import tool_agent
from connoisseur.log import get_logger
from connoisseur.observability import traced

log = get_logger(__name__)

Text = Annotated[str, Field(min_length=1, max_length=200)]


@dataclass
class SkillResult:
    answer: str = ""
    cards: list[dict] = field(default_factory=list)
    route: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    location: Any = None
    final: bool = False  # a question back to the user ends the turn


# ---------- arguments (the tool schemas the model sees) ----------

class search_restaurants(BaseModel):  # the class name is the tool name
    """Find restaurants for the user: by cuisine, vibe, diet or budget, in a city or near the user."""
    what: Text = Field(description="What to look for in English, e.g. 'quiet vegan cafe' or 'Italian'")
    where: str | None = Field(default=None, max_length=120,
                              description="City or neighbourhood if the user named one in this message")
    near_me: bool = Field(default=False, description="True when the user wants places near their position")


class place_info(BaseModel):
    """Answer a question about ONE restaurant: facts, reviews, or which dishes to order there. Works for
    restaurants recommended before ("the second one") and for restaurants the user names."""
    place: Text = Field(description="Restaurant name, or the user's words pointing to it ('the second one')")
    question: Text = Field(description="What the user wants to know, e.g. 'which dishes to order'")


class search_recipes(BaseModel):
    """Find recipes to cook. With a specific dish, gets that recipe (from the collection, or from the web
    when the collection does not have it or the user asks for the internet)."""
    what: Text = Field(description="What the user wants to cook, in English, e.g. 'easy pasta dinner'")
    dish: str | None = Field(default=None, max_length=100, description="A specific dish in English, e.g. 'ajiaco'")
    from_web: bool = Field(default=False, description="True when the user asks to search the internet")


class recipe_steps(BaseModel):
    """Ingredients and preparation steps of ONE recipe recommended before ('the second one') or named."""
    recipe: Text = Field(description="Recipe name, or the user's words pointing to it ('the second one')")


class ask_user(BaseModel):
    """Ask the user for a missing detail instead of guessing. Ends the turn."""
    kind: Literal["location", "which_place", "which_recipe"]


class answer_canned(BaseModel):
    """Greetings and what you can do ('help'), requests outside food ('out_of_scope'), or editing the
    restaurant database ('database')."""
    kind: Literal["help", "out_of_scope", "database"]


TOOLS = (search_restaurants, place_info, search_recipes, recipe_steps, ask_user, answer_canned)


# ---------- helpers ----------

def _with(state: ChatState, **context: Any) -> dict:
    """A working copy of the state for one skill: its own context and an empty route to collect markers."""
    ctx = state["context"].model_copy(update=context)
    return {**state, "context": ctx, "route": [], "answer": "", "cards": [], "evidence": []}


def _names(cards: list[dict], kind: str) -> list[str]:
    # Names can come from web pages: short and without angle brackets (the summary is tagged as data)
    return [c["name"].replace("<", "").replace(">", "")[:60] for c in cards if c.get("type") == kind]


def _result(st: dict, summary: dict, **extra: Any) -> SkillResult:
    return SkillResult(answer=st.get("answer") or "", cards=st.get("cards") or [], route=st.get("route") or [],
                       evidence=st.get("evidence") or [], summary=summary, **extra)


async def _experts_and_recommend(st: dict) -> dict:
    """Experts in parallel (as the graph's fan-out does), then the grounded recommender."""
    experts = nodes.route_to_experts(st)
    if isinstance(experts, list):
        for delta in await asyncio.gather(*(getattr(nodes, name)(st) for name in experts)):
            st |= delta
    st |= await nodes.recommend(st)
    return st


# ---------- skills ----------

@traced("skill_search_restaurants", as_type="tool")
async def run_search_restaurants(state: ChatState, args: search_restaurants) -> SkillResult:
    ctx = state["context"]
    st = _with(state, intent="restaurant", search_query=args.what,
               location=args.where or ctx.location, near_me=args.near_me or ctx.near_me)
    st |= await nodes.locate(st)
    step = nodes.route_after_locate(st)
    if step == "ask_location":
        st |= await nodes.ask_location(st)
        return _result(st, {"status": "needs_location"}, final=True)
    if step == "retrieve_local":
        st |= await nodes.retrieve_local(st)
        if nodes.route_after_local(st) == "web_search":
            st |= await nodes.web_search(st)
    else:
        st |= await nodes.web_search(st)
    st = await _experts_and_recommend(st)
    location = st.get("location")
    names = _names(st.get("cards") or [], "restaurant")
    return _result(st, {"status": "found" if names else "none", "restaurants": names,
                        "location": location.display_name if location else None}, location=location)


def _named(name: str, state: ChatState) -> str | None:
    """The name the model passed, unless the user gave a position ("el segundo") and did not write that name:
    then code resolves the position. Nova 2 Lite turned "¿qué pido en el segundo?" into the first place."""
    if nodes._ordinal(state["message"]) is not None and nodes.ratings.fold(name) not in nodes.ratings.fold(
            state["message"]):
        return None
    return name


@traced("skill_place_info", as_type="tool")
async def run_place_info(state: ChatState, args: place_info) -> SkillResult:
    # The question becomes the message the lookup nodes read (dish questions, pointers like "allí")
    st = _with(state, intent="lookup", restaurant_name=_named(args.place, state))
    st["message"] = f"{state['message']}\n{args.question}"
    step = nodes.route_intent(st)
    if step == "recall":
        st |= await nodes.recall(st)
    else:
        st |= await tool_agent(st)
    status = "needs_which_place" if "recall:no_reference" in st["route"] else "answered"
    return _result(st, {"status": status, "place": args.place}, final=status != "answered")


@traced("skill_search_recipes", as_type="tool")
async def run_search_recipes(state: ChatState, args: search_recipes) -> SkillResult:
    st = _with(state, intent="recipe", search_query=args.what, dish=args.dish)
    if args.from_web:
        st["message"] = f"{state['message']} (web)"  # wants_web reads the message
    step = nodes._route_recipe(st)
    if step == "recipe_detail":
        st |= await nodes.recipe_detail(st)
    else:
        if step == "web_recipe":
            st |= await nodes.web_recipe(st)
        if not st.get("answer"):
            st |= await nodes.retrieve_local(st)
            st = await _experts_and_recommend(st)
    names = _names(st.get("cards") or [], "recipe")
    source = "web" if "web_recipe" in st["route"] else "collection"
    return _result(st, {"status": "found" if names else "none", "recipes": names, "source": source})


@traced("skill_recipe_steps", as_type="tool")
async def run_recipe_steps(state: ChatState, args: recipe_steps) -> SkillResult:
    st = _with(state, intent="recipe", dish=_named(args.recipe, state))
    st |= await nodes.recipe_detail(st)
    found = "recipe_detail" in st["route"]
    if not found and not nodes._is_reference(args.recipe):
        # A dish that was not shown and is not in the collection ("¿cómo se hace el ajiaco?"): find it
        return await run_search_recipes(state, search_recipes(what=args.recipe, dish=args.recipe))
    return _result(st, {"status": "found" if found else "needs_which_recipe",
                        "recipe": _names(st.get("cards") or [], "recipe")}, final=not found)


ASK_TEXT = {"location": "ask_location", "which_place": "which_place", "which_recipe": "which_recipe"}


async def run_ask_user(state: ChatState, args: ask_user) -> SkillResult:
    return SkillResult(answer=nodes.t(state["context"], ASK_TEXT[args.kind]), route=[f"ask:{args.kind}"],
                       summary={"status": "asked"}, final=True)


async def run_answer_canned(state: ChatState, args: answer_canned) -> SkillResult:
    st = _with(state, intent=args.kind if args.kind != "help" else "clarification")
    st |= await nodes.respond(st)
    return _result(st, {"status": "answered"}, final=True)


RUNNERS = {
    "search_restaurants": run_search_restaurants,
    "place_info": run_place_info,
    "search_recipes": run_search_recipes,
    "recipe_steps": run_recipe_steps,
    "ask_user": run_ask_user,
    "answer_canned": run_answer_canned,
}
SCHEMAS = {tool.__name__: tool for tool in TOOLS}


def tool_definitions() -> list[dict]:
    """Function definitions for `bind_tools` (same shape the tool agent uses for the MCP tools)."""
    return [{"type": "function", "function": {"name": name, "description": (tool.__doc__ or "").strip(),
                                              "parameters": tool.model_json_schema()}}
            for name, tool in SCHEMAS.items()]


async def run(state: ChatState, name: str, raw_args: dict) -> SkillResult:
    """Validate the model's arguments and run the skill. Unknown tools and bad arguments come back as a
    summary the model can read, never as an exception."""
    if name not in RUNNERS:
        return SkillResult(summary={"status": "error", "error": f"unknown tool {name}"}, route=["orch:bad_tool"])
    try:
        args = SCHEMAS[name].model_validate(raw_args or {})
    except ValueError as exc:
        log.info("orchestrator_bad_args", tool=name, error=str(exc)[:300])
        return SkillResult(summary={"status": "error", "error": str(exc)[:200]}, route=["orch:bad_args"])
    return await RUNNERS[name](state, args)


def summary_message(result: SkillResult) -> str:
    return json.dumps(result.summary, ensure_ascii=False)
