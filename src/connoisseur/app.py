"""Gradio developer UI: chats with the LangGraph workflow in-process and shows each agent step.

Standalone:   python -m connoisseur.app [--share]
Inside API:   uvicorn connoisseur.api:app  ->  http://localhost:8000/ui
"""
import argparse
import os

os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")  # no telemetry from the dev UI

import gradio as gr

from connoisseur.agents.runner import stream_chat

EXAMPLES = [
    "Un café vegano tranquilo en El Poblado, Medellín",
    "Find me a moody Italian spot in Pasadena",
    "Tell me about Sakura Garden and its review",
    "What should I cook tonight? Something with noodles",
]


async def handle_chat(user_message, history):
    history = history or []
    if not user_message or not user_message.strip():
        yield history, ""
        return

    previous = [{"role": m["role"], "content": m["content"]} for m in history
                if isinstance(m.get("content"), str)]
    history = history + [
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": "_Thinking..._"},
    ]
    yield history, ""

    steps: list[str] = []
    async for event in stream_chat(user_message, previous):
        if event["type"] == "step":
            steps.append(event["label"])
            history[-1] = {"role": "assistant", "content": "_" + " → ".join(steps) + "..._"}
        elif event["type"] == "answer":
            footer = f"\n\n<sub>route: {' · '.join(event['route'])} · {event['seconds']}s</sub>"
            history[-1] = {"role": "assistant", "content": (event["answer"] or "(no answer)") + footer}
        yield history, ""


def build_demo() -> gr.Blocks:
    with gr.Blocks(title="Connoisseur Companion") as demo:
        gr.Markdown(
            "# Connoisseur Companion\n"
            "Restaurants anywhere (California guide + OpenStreetMap), recipes and reviews. "
            "Tell me where you are and what you feel like eating."
        )
        chatbot = gr.Chatbot(height=520)
        msg_input = gr.Textbox(label="Your message", placeholder=EXAMPLES[0])
        gr.Examples(EXAMPLES, inputs=msg_input)
        msg_input.submit(handle_chat, [msg_input, chatbot], [chatbot, msg_input])
    return demo


def main():
    parser = argparse.ArgumentParser(description="Connoisseur Companion developer UI")
    parser.add_argument("--share", action="store_true", help="create a public Gradio link")
    args = parser.parse_args()
    build_demo().launch(share=args.share, theme=gr.themes.Soft())


if __name__ == "__main__":
    main()
