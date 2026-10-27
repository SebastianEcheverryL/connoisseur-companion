"""ReAct agent over the MCP server's tools, for questions about specific restaurants and reviews.

The MCP server runs in-process (FastMCP in-memory transport): no subprocess per message.
"""
import asyncio
import json

from fastmcp import Client
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from connoisseur.agents import grounding
from connoisseur.agents.prompts import load_prompt
from connoisseur.agents.state import ChatState
from connoisseur.llm import get_chat_model, message_text
from connoisseur.log import get_logger
from connoisseur.observability import traced
from connoisseur.security import spotlight
from connoisseur.server import READ_ONLY_TOOLS, mcp
from connoisseur.settings import get_settings

log = get_logger(__name__)
MAX_TOOL_OUTPUT_CHARS = 4000


def _history_messages(state: ChatState) -> list:
    messages = []
    for turn in (state.get("history") or [])[-6:]:
        content = turn.get("content")
        if not content:
            continue
        messages.append(HumanMessage(content=content) if turn.get("role") == "user" else AIMessage(content=content))
    return messages


def _previous_recommendations(state: ChatState) -> str:
    """Places shown earlier, so "tell me more about the second one" works for web and OSM places too
    (the guide's tools only know the curated dataset). Names and links can come from the web: tagged as data."""
    cards = state.get("last_cards") or []
    if not cards:
        return ""
    rows = [json.dumps(card.model_dump(exclude_none=True), ensure_ascii=False).replace("<", "").replace(">", "")
            for card in cards]
    return ("Recommendations already shown in this conversation, numbered per type as the user saw them. "
            "Places from the guide (id starting with dataset:) can be looked up with the tools; for the others, "
            "answer only from these details:\n" + spotlight.tag("\n".join(rows), source="previous_answer"))


@traced("mcp_tool_call", as_type="tool")
async def call_mcp_tool(client: Client, name: str, args: dict) -> str:
    """Call a tool with a timeout and return its output screened and wrapped as untrusted data.

    Tool results contain user-generated text (reviews, descriptions): an injection hidden there must
    not be able to steer the agent (OWASP LLM01, indirect prompt injection).
    """
    try:
        result = await asyncio.wait_for(client.call_tool(name, args, raise_on_error=False),
                                        timeout=get_settings().tool_timeout_s)
    except TimeoutError:
        log.warning("tool_timeout", tool=name)
        return f"Tool '{name}' timed out."
    output = " ".join(getattr(item, "text", str(item)) for item in result.content) or "(no result)"
    return spotlight.wrap(output[:MAX_TOOL_OUTPUT_CHARS], source=f"tool:{name}")


async def tool_agent(state: ChatState) -> dict:
    max_steps = get_settings().max_react_steps
    async with Client(mcp) as client:
        # Allowlist: the agent only ever sees read-only tools
        tools = [t for t in await client.list_tools() if t.name in READ_ONLY_TOOLS]
        tool_definitions = [
            {
                "type": "function",
                "function": {"name": t.name, "description": t.description or "", "parameters": t.inputSchema},
            }
            for t in tools
        ]
        model = get_chat_model().bind_tools(tool_definitions)
        previous = _previous_recommendations(state)
        system = load_prompt("tool_agent") + (f"\n\n{previous}" if previous else "")
        messages = [SystemMessage(content=system), *_history_messages(state), HumanMessage(content=state["message"])]
        tools_used: list[str] = []
        # Facts about earlier picks may support the answer (the grounding check must accept them)
        evidence: list[str] = [previous] if previous else []

        for _ in range(max_steps):
            response = await model.ainvoke(messages)
            messages.append(response)
            if not response.tool_calls:
                answer = message_text(response.content)
                route = state["route"] + tools_used
                if get_settings().enable_grounding_check and evidence:
                    answer, revised = await grounding.enforce(state["message"], answer, evidence)
                    route.append("grounding:revised" if revised else "grounding:ok")
                return {"answer": answer, "route": route, "evidence": evidence}

            for call in response.tool_calls:
                tools_used.append(f"tool:{call['name']}")
                if call["name"] not in READ_ONLY_TOOLS:
                    output = f"Tool '{call['name']}' is not available."
                else:
                    output = await call_mcp_tool(client, call["name"], call["args"])
                    evidence.append(output)
                messages.append(ToolMessage(content=output, tool_call_id=call["id"]))

    log.warning("react_step_limit_reached", steps=max_steps)
    return {
        "answer": "I wasn't able to complete that request. Please try rephrasing it.",
        "route": state["route"] + tools_used + ["step_limit"],
        "evidence": evidence,
    }
