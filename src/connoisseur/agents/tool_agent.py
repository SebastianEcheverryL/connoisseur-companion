"""ReAct agent over the MCP server's tools, for questions about specific restaurants and reviews.

The MCP server runs in-process (FastMCP in-memory transport): no subprocess per message.
"""
import asyncio

from fastmcp import Client
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

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
        messages = [SystemMessage(content=load_prompt("tool_agent")), *_history_messages(state),
                    HumanMessage(content=state["message"])]
        tools_used = []

        for _ in range(max_steps):
            response = await model.ainvoke(messages)
            messages.append(response)
            if not response.tool_calls:
                return {"answer": message_text(response.content), "route": state["route"] + tools_used}

            for call in response.tool_calls:
                tools_used.append(f"tool:{call['name']}")
                if call["name"] not in READ_ONLY_TOOLS:
                    output = f"Tool '{call['name']}' is not available."
                else:
                    output = await call_mcp_tool(client, call["name"], call["args"])
                messages.append(ToolMessage(content=output, tool_call_id=call["id"]))

    log.warning("react_step_limit_reached", steps=max_steps)
    return {
        "answer": "I wasn't able to complete that request. Please try rephrasing it.",
        "route": state["route"] + tools_used + ["step_limit"],
    }
