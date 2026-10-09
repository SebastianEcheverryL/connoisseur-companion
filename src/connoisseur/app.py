"""Gradio chat UI acting as an MCP host: a ReAct loop over the server's tools."""
import argparse
import os
from pathlib import Path

import gradio as gr
from fastmcp.client import Client, PythonStdioTransport
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_ollama import ChatOllama

from connoisseur.config import CHAT_MODEL, PROJECT_DIR

SERVER_SCRIPT = str(Path(__file__).parent / "server.py")
MAX_REACT_STEPS = 10
SYSTEM_PROMPT = (
    "You are a food recommendation expert with expertise in global cuisines. "
    "You can recommend restaurants and recipes based on user preferences and dietary restrictions. "
    "Use the available tools to look up restaurants, vibes and reviews before answering."
)


def make_model() -> ChatOllama:
    return ChatOllama(model=CHAT_MODEL, temperature=0.7)


async def chat_with_agent(user_message: str, history: list) -> str:
    """Connect to the MCP server, discover tools, and run a ReAct loop.
    The LLM decides which tools to call, calls them via the MCP server,
    and repeats until it produces a final text response."""
    transport = PythonStdioTransport(
        script_path=SERVER_SCRIPT,
        env={**os.environ, "PYTHONPATH": str(PROJECT_DIR / "src")},
    )

    async with Client(transport) as client:
        mcp_tools = await client.list_tools()

        # Convert MCP tool schemas to OpenAI-style tool definitions for the LLM
        tool_definitions = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description or "",
                    "parameters": t.inputSchema,
                },
            }
            for t in mcp_tools
        ]

        model = make_model().bind_tools(tool_definitions)

        messages = [SystemMessage(content=SYSTEM_PROMPT)]
        for msg in history:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user" and content:
                messages.append(HumanMessage(content=content))
            elif role == "assistant" and content:
                messages.append(AIMessage(content=content))
        messages.append(HumanMessage(content=user_message))

        # ReAct loop: call tools until the LLM returns a plain text reply
        for _ in range(MAX_REACT_STEPS):
            response = await model.ainvoke(messages)
            messages.append(response)

            if not response.tool_calls:
                raw = response.content
                if isinstance(raw, list):
                    return " ".join(
                        b.get("text", "") if isinstance(b, dict) else str(b)
                        for b in raw
                    )
                return str(raw)

            for tool_call in response.tool_calls:
                result = await client.call_tool(tool_call["name"], tool_call["args"])
                tool_output = (
                    " ".join(
                        item.text if hasattr(item, "text") else str(item)
                        for item in result.content
                    )
                    if result.content
                    else "(no result)"
                )
                messages.append(ToolMessage(content=tool_output, tool_call_id=tool_call["id"]))

        return "I wasn't able to complete that request. Please try again."


async def handle_chat(user_message, history):
    if history is None:
        history = []
    if not user_message or not user_message.strip():
        yield history
        return

    # Show a thinking placeholder while the agent runs
    history = history + [
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": "Thinking..."},
    ]
    yield history

    response_text = await chat_with_agent(user_message, history[:-2])
    history[-1] = {"role": "assistant", "content": response_text}
    yield history


def build_demo() -> gr.Blocks:
    with gr.Blocks(title="Connoisseur Companion") as demo:
        gr.Markdown(
            "# Connoisseur Companion\n"
            "Your AI guide to California's restaurant scene. "
            "Ask me about restaurants by name, cuisine, or vibe!"
        )

        chatbot = gr.Chatbot(height=500)
        msg_input = gr.Textbox(
            label="Ask about restaurants",
            placeholder='e.g., "Find me a moody spot in DTLA" or "Tell me about Sakura Garden"',
        )

        with gr.Row():
            btn1 = gr.Button("Find moody restaurants", size="sm")
            btn2 = gr.Button("Tell me about Iron & Embers", size="sm")
            btn3 = gr.Button("Zen dining in Little Tokyo?", size="sm")

        msg_input.submit(handle_chat, [msg_input, chatbot], [chatbot])
        msg_input.submit(lambda: "", None, msg_input)

        btn1.click(handle_chat, [gr.State("Find me some moody restaurants"), chatbot], [chatbot])
        btn2.click(handle_chat, [gr.State("Tell me about Iron & Embers"), chatbot], [chatbot])
        btn3.click(
            handle_chat,
            [gr.State("What's a zen dining experience in Little Tokyo?"), chatbot],
            [chatbot],
        )
    return demo


def main():
    parser = argparse.ArgumentParser(description="Connoisseur Companion chat UI")
    parser.add_argument("--share", action="store_true", help="create a public Gradio link")
    args = parser.parse_args()

    print("Starting Connoisseur Companion...")
    build_demo().launch(share=args.share, theme=gr.themes.Soft())


if __name__ == "__main__":
    main()
