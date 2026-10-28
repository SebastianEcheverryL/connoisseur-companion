"""Hybrid orchestrator: an LLM decides which high-level tools (agents/skills.py) answer each message,
instead of the fixed intent → branch routing. It can use one tool, several in a row ("an Italian place
in Pasadena and what to order there"), or ask the user for a missing detail.

What keeps it safe and grounded:
- every tool is deterministic inside and writes its own part of the answer; the orchestrator's text is
  discarded, so it cannot state facts;
- it sees only short summaries of the tool results (counts and names, tagged as data), never review or
  page text: content from the web cannot steer which tool runs next;
- at most `orchestrator_max_steps` tool calls; arguments are validated; unknown tools are refused;
- when it fails or chooses no tool, the graph falls back to the fixed routing (route_intent).
"""
import json
import re

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langgraph.config import get_stream_writer

from connoisseur import memory
from connoisseur.agents import nodes, skills
from connoisseur.agents.prompts import load_prompt
from connoisseur.agents.state import ChatState
from connoisseur.agents.tool_agent import _history_messages
from connoisseur.llm import get_chat_model
from connoisseur.log import get_logger
from connoisseur.security import spotlight
from connoisseur.settings import get_settings

log = get_logger(__name__)


def _emit(node: str) -> None:
    """Progress event for the UI ("Searching restaurants…"); a no-op outside a graph run (tests)."""
    try:
        get_stream_writer()({"node": node})
    except RuntimeError:
        pass


def _system(state: ChatState) -> str:
    ctx = state["context"]
    understood = ctx.model_dump(include={"intent", "language", "location", "near_me", "cuisines", "dietary",
                                         "restaurant_name", "dish"}, exclude_none=True)
    understood["shared_position"] = bool(state.get("coords"))
    return (f"{load_prompt('orchestrator')}\n\nWhat the profile agent understood: "
            f"{json.dumps(understood, ensure_ascii=False)}\n\n{nodes._memory_block(state)}".rstrip())


def compose(state: ChatState, results: list[tuple[str, skills.SkillResult]]) -> dict:
    """One answer from the sections in call order. A note several sections end with (the allergy note,
    the OpenStreetMap credit) is kept once."""
    sections, notes = [], set()
    for _, result in results:
        lines = []
        for line in result.answer.splitlines():
            stripped = line.strip()
            if stripped.endswith("_") and stripped.startswith(("_", "⚠️ _")):
                if stripped in notes:
                    continue
                notes.add(stripped)
            lines.append(line)
        if section := "\n".join(lines).strip():
            sections.append(section)
    route = state["route"] + ["orch"]
    for name, result in results:
        route += [f"orch:{name}", *result.route]
    out = {"answer": "\n\n".join(sections), "cards": [card for _, r in results for card in r.cards],
           "route": route, "evidence": [e for _, r in results for e in r.evidence]}
    if location := next((r.location for _, r in results if r.location is not None), None):
        out["location"] = location
    return out


_SEVERAL = re.compile(
    r"\b(and|y|e)\s+(tell|dime|dame|give|show|mu[eé]strame|what|qu[eé]|how|c[oó]mo|which|cu[aá]l|"
    r"recomi[eé]nd\w*|recommend|busca\w*|find|search)\b|\b(tambi[eé]n|adem[aá]s|then|luego|despu[eé]s|also)\b",
    re.IGNORECASE,
)


def _asks_several(message: str) -> bool:
    """"Find an Italian place and tell me what to order there": two requests in one message."""
    return bool(_SEVERAL.search(message))


def _recipe_already_shown(call: dict, results: list[tuple[str, skills.SkillResult]], message: str) -> bool:
    """Steps right after a recipe search, in the same turn, only when the user asked how to cook. Nova 2 Lite
    asked for the steps of an ajiaco search_recipes had just written out in full (shown twice), and added the
    steps of the first pasta to a plain "dame una receta de pasta" list, ignoring the prompt."""
    if call["name"] != "recipe_steps":
        return False
    shown = any(marker in ("recipe_detail", "web_recipe") for _, result in results for marker in result.route)
    searched = any(name == "search_recipes" for name, _ in results)
    return shown or (searched and not nodes.asks_how_to_cook(message))


async def orchestrator(state: ChatState) -> dict:
    max_steps = get_settings().orchestrator_max_steps
    model = get_chat_model(temperature=0).bind_tools(skills.tool_definitions())
    messages = [SystemMessage(content=_system(state)), *_history_messages(state),
                HumanMessage(content=state["message"])]
    results: list[tuple[str, skills.SkillResult]] = []
    done: set[str] = set()
    working = dict(state)
    rounds = 0
    try:
        for _ in range(max_steps):  # rounds, so a model that repeats a skipped call cannot loop forever
            if len(results) >= max_steps:
                break
            response = await model.ainvoke(messages)
            messages.append(response)
            if not response.tool_calls:
                break
            finished, retry = False, False
            for call in response.tool_calls:
                key = json.dumps([call["name"], call["args"]], sort_keys=True, ensure_ascii=False)
                if (finished or len(results) >= max_steps or key in done
                        or _recipe_already_shown(call, results, state["message"])):
                    # Every tool call needs a result before the model is called again
                    result = skills.SkillResult(summary={"status": "skipped"})
                else:
                    done.add(key)
                    _emit(call["name"])
                    result = await skills.run(working, call["name"], call["args"])
                    results.append((call["name"], result))
                    finished = result.final
                    retry = retry or result.summary.get("status") == "error"
                    if result.cards:
                        # "an Italian place and what to order at the first one": later tools see these picks
                        working = {**working, "last_cards": memory.card_refs(result.cards)}
                messages.append(ToolMessage(content=spotlight.tag(skills.summary_message(result),
                                                                  source=f"tool:{call['name']}"),
                                            tool_call_id=call["id"]))
            # Plan, then execute: references such as "the first one" are resolved in code against this turn's
            # picks, so the first answer can plan every call. Asking the model again only to hear it is done
            # doubled the answer time on the multi-turn eval. Another round only to fix an error, or when the
            # message asks for several things and only one call came (Nova 2 Lite plans one call at a time).
            asked_more = rounds == 0 and len(response.tool_calls) == 1 and _asks_several(state["message"])
            if finished or not (retry or asked_more):
                break
            rounds += 1
    except Exception as exc:  # noqa: BLE001 - the fixed routing still answers
        log.warning("orchestrator_failed", error=str(exc)[:300])
        return {"route": state["route"] + ["orch:fallback"]}
    if not any(result.answer for _, result in results):
        log.info("orchestrator_fallback", tools=[name for name, _ in results])
        return {"route": state["route"] + ["orch:fallback"]}
    log.info("orchestrator", tools=[name for name, _ in results])
    return compose(state, results)
