"""The multi-agent workflow (ported from notebook 07, with real retrieval and a web-search branch).

input_guard ── (blocked) ── END
     │
profile ─┬─ respond                                   (help, refusals)
         ├─ tool_agent                                (lookups through MCP tools)
         ├─ recall                                    (follow-ups on earlier web/OSM picks)
         ├─ recipe_detail                             (steps of a recipe shown or named)
         ├─ web_recipe ─── (nothing usable) ──┐       (a dish the dataset lacks)
         ├─ retrieve_local ◄──────────────────┤       (recipes)
         └─ locate ─┬─ ask_location                   ("near me" without a position)
                    ├─ retrieve_local ─┬──┤           (California)
                    │                  └─ web_search  (too few local results)
                    └─ web_search ─────────┤          (outside California)
                                           ▼
                   trend_analyst ∥ style_expert ∥ nutrition_expert  →  recommend ─┐
                                                                tool_agent ─┴─ output_guard ── END

With ORCHESTRATOR_ENABLED, profile goes to the orchestrator instead (agents/orchestrator.py): an LLM picks
the tools in agents/skills.py, which run these same branches, and composes their sections. If it fails or
chooses no tool, the fixed routing above answers.
"""
from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from connoisseur.agents import guards, nodes
from connoisseur.agents.orchestrator import orchestrator
from connoisseur.agents.state import ChatState
from connoisseur.agents.tool_agent import tool_agent
from connoisseur.settings import get_settings

EXPERTS = ("trend_analyst", "style_expert", "nutrition_expert")
DIRECT_INTENTS = ("clarification", "out_of_scope", "database")


def after_profile(state: ChatState) -> str:
    """The orchestrator chooses the tools when enabled; otherwise the intent picks one fixed branch.
    Greetings and refusals get their fixed text directly: no tool to choose (Nova 2 Lite answered "hola"
    with plain text, which cost a call before the fallback)."""
    if get_settings().orchestrator_enabled and state["context"].intent not in DIRECT_INTENTS:
        return "orchestrator"
    return nodes.route_intent(state)


def after_orchestrator(state: ChatState) -> str:
    """The orchestrator's composed answer goes to the output guard; if it gave up, the fixed routing answers."""
    return "output_guard" if state.get("answer") else nodes.route_intent(state)


def build_graph():
    g = StateGraph(ChatState)
    g.add_node("input_guard", guards.input_guard)
    g.add_node("output_guard", guards.output_guard)
    g.add_node("profile", nodes.profile)
    g.add_node("respond", nodes.respond)
    g.add_node("tool_agent", tool_agent)
    g.add_node("recall", nodes.recall)
    g.add_node("recipe_detail", nodes.recipe_detail)
    g.add_node("web_recipe", nodes.web_recipe)
    g.add_node("locate", nodes.locate)
    g.add_node("ask_location", nodes.ask_location)
    g.add_node("retrieve_local", nodes.retrieve_local)
    g.add_node("web_search", nodes.web_search)
    g.add_node("experts", lambda state: {})  # fan-out point for the parallel analyses
    g.add_node("trend_analyst", nodes.trend_analyst)
    g.add_node("style_expert", nodes.style_expert)
    g.add_node("nutrition_expert", nodes.nutrition_expert)
    g.add_node("recommend", nodes.recommend)

    g.add_edge(START, "input_guard")
    g.add_conditional_edges("input_guard", guards.route_after_input_guard, {"blocked": END, "profile": "profile"})
    branches = ["locate", "retrieve_local", "tool_agent", "recall", "recipe_detail", "web_recipe", "respond"]
    g.add_node("orchestrator", orchestrator)
    g.add_conditional_edges("profile", after_profile, [*branches, "orchestrator"])
    g.add_conditional_edges("orchestrator", after_orchestrator, [*branches, "output_guard"])
    g.add_edge("recipe_detail", "output_guard")
    g.add_conditional_edges("web_recipe", nodes.route_after_web_recipe, ["output_guard", "retrieve_local"])
    g.add_conditional_edges("locate", nodes.route_after_locate, ["retrieve_local", "web_search", "ask_location"])
    g.add_edge("ask_location", END)
    g.add_conditional_edges("retrieve_local", nodes.route_after_local, ["web_search", "experts"])
    g.add_edge("web_search", "experts")
    g.add_conditional_edges("experts", nodes.route_to_experts, [*EXPERTS, "recommend"])
    g.add_edge(list(EXPERTS), "recommend")  # join: recommend waits for all three experts
    g.add_edge("recommend", "output_guard")
    g.add_edge("respond", END)
    g.add_edge("tool_agent", "output_guard")
    g.add_edge("recall", "output_guard")
    g.add_edge("output_guard", END)
    return g.compile()


@lru_cache
def get_graph():
    return build_graph()
