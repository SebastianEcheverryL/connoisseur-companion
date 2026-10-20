"""Gradio developer UI: chats with the LangGraph workflow in-process and shows each agent step.

Standalone:   python -m connoisseur.app [--share]
Inside API:   uvicorn connoisseur.api:app  ->  http://localhost:8000/ui
"""
import argparse
import os

os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")  # no telemetry from the dev UI

import gradio as gr

from connoisseur.agents.runner import stream_chat
from connoisseur.schemas import Coordinates

EXAMPLES = [
    "Un café vegano tranquilo en El Poblado, Medellín",
    "Recomiéndame sushi cerca de mí",
    "Find me a moody Italian spot in Pasadena",
    "Tell me about Sakura Garden and its review",
    "What should I cook tonight? Something with noodles",
]


# Runs in the browser: asks for permission, rounds to 2 decimals (~1 km) before anything leaves the page
GEOLOCATION_JS = """
async () => new Promise((resolve) => {
  if (!navigator.geolocation) { resolve("unavailable"); return; }
  navigator.geolocation.getCurrentPosition(
    (p) => resolve(p.coords.latitude.toFixed(2) + ", " + p.coords.longitude.toFixed(2)),
    () => resolve("permission denied"),
    {timeout: 10000, maximumAge: 600000}
  );
})
"""


def parse_coords(text: str | None) -> Coordinates | None:
    """Parse the "lat, lon" string filled by the location button; anything else means no location."""
    try:
        lat, lon = (float(part) for part in (text or "").split(","))
        return Coordinates(lat=lat, lon=lon).rounded()
    except ValueError:
        return None


async def handle_chat(user_message, history, shared_location=None):
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
    async for event in stream_chat(user_message, previous, parse_coords(shared_location)):
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
        with gr.Row():
            location_box = gr.Textbox(
                label="Shared location (rounded to ~1 km)",
                placeholder="Not shared: the assistant will ask where you are",
                interactive=False,
                scale=4,
            )
            locate_btn = gr.Button("📍 Use my location", scale=1)
            clear_btn = gr.Button("Stop sharing", scale=1)
        gr.Examples(EXAMPLES, inputs=msg_input)
        locate_btn.click(None, None, location_box, js=GEOLOCATION_JS)
        clear_btn.click(lambda: "", None, location_box)
        msg_input.submit(handle_chat, [msg_input, chatbot, location_box], [chatbot, msg_input])
    return demo


def main():
    parser = argparse.ArgumentParser(description="Connoisseur Companion developer UI")
    parser.add_argument("--share", action="store_true", help="create a public Gradio link")
    args = parser.parse_args()
    build_demo().launch(share=args.share, theme=gr.themes.Soft())


if __name__ == "__main__":
    main()
