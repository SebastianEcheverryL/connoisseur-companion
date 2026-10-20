"""The multi-agent workflow (ported from notebook 07, with real retrieval and a web-search branch).

profile ─┬─ respond                                   (help, refusals)
         ├─ tool_agent                                (lookups through MCP tools)
         ├─ retrieve_local ───────────────┐           (recipes)
         └─ locate ─┬─ ask_location                   ("near me" without a position)
                    ├─ retrieve_local ─┬──┤           (California)
                    │                  └─ web_search  (too few local results)
                    └─ web_search ─────────┤          (outside California)
                                           ▼
                   trend_analyst ∥ style_expert ∥ nutrition_expert  →  recommend
"""
from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from connoisseur.agents import nodes
from connoisseur.agents.state import ChatState
from connoisseur.agents.tool_agent import tool_agent

EXPERTS = ("trend_analyst", "style_expert", "nutrition_expert")


def build_graph():
    g = StateGraph(ChatState)
    g.add_node("profile", nodes.profile)
    g.add_node("respond", nodes.respond)
    g.add_node("tool_agent", tool_agent)
    g.add_node("locate", nodes.locate)
    g.add_node("ask_location", nodes.ask_location)
    g.add_node("retrieve_local", nodes.retrieve_local)
    g.add_node("web_search", nodes.web_search)
    g.add_node("experts", lambda state: {})  # fan-out point for the parallel analyses
    g.add_node("trend_analyst", nodes.trend_analyst)
    g.add_node("style_expert", nodes.style_expert)
    g.add_node("nutrition_expert", nodes.nutrition_expert)
    g.add_node("recommend", nodes.recommend)

    g.add_edge(START, "profile")
    g.add_conditional_edges("profile", nodes.route_intent, ["locate", "retrieve_local", "tool_agent", "respond"])
    g.add_conditional_edges("locate", nodes.route_after_locate, ["retrieve_local", "web_search", "ask_location"])
    g.add_edge("ask_location", END)
    g.add_conditional_edges("retrieve_local", nodes.route_after_local, ["web_search", "experts"])
    g.add_edge("web_search", "experts")
    g.add_conditional_edges("experts", nodes.route_to_experts, [*EXPERTS, "recommend"])
    g.add_edge(list(EXPERTS), "recommend")  # join: recommend waits for all three experts
    g.add_edge("recommend", END)
    g.add_edge("respond", END)
    g.add_edge("tool_agent", END)
    return g.compile()


@lru_cache
def get_graph():
    return build_graph()
