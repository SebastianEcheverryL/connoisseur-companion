"""Guardrail nodes: screen the user message before any agent runs, and LLM answers before they are shown."""
from connoisseur.agents.nodes import guess_language, t, t_lang
from connoisseur.agents.state import ChatState
from connoisseur.security import guardrails


async def input_guard(state: ChatState) -> dict:
    result = await guardrails.check_input(state["message"])
    if result.allowed:
        return {"route": state.get("route", []) + ["guard:input:ok"]}
    key = "blocked_injection" if result.category == "prompt_injection" else "blocked_input"
    return {
        "answer": t_lang(guess_language(state["message"]), key),
        "cards": [],
        "blocked": True,
        "route": state.get("route", []) + [f"guard:input:blocked:{result.category}"],
    }


def route_after_input_guard(state: ChatState) -> str:
    return "blocked" if state.get("blocked") else "profile"


async def output_guard(state: ChatState) -> dict:
    """Only LLM-written answers come here; fixed texts (help, refusals, questions) skip this node."""
    answer = state.get("answer") or ""
    if not answer:
        return {}
    result = await guardrails.check_output(state["message"], answer)
    if result.allowed:
        return {"route": state["route"] + ["guard:output:ok"]}
    return {
        "answer": t(state.get("context"), "blocked_output"),
        "cards": [],
        "route": state["route"] + [f"guard:output:blocked:{result.category}"],
    }
