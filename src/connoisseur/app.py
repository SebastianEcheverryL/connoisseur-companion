"""Gradio developer UI: chats with the LangGraph workflow in-process and shows each agent step.

Standalone:   python -m connoisseur.app [--share]
Inside API:   uvicorn connoisseur.api:app  ->  http://localhost:8000/ui
"""
import argparse
import os
import uuid

os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")  # no telemetry from the dev UI

import gradio as gr

from connoisseur import observability
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


async def handle_chat(user_message, history, shared_location=None, session_id=None, trace_ids=None):
    history = history or []
    trace_ids = dict(trace_ids or {})
    if not user_message or not user_message.strip():
        yield history, "", trace_ids
        return

    previous = [{"role": m["role"], "content": m["content"]} for m in history
                if isinstance(m.get("content"), str)]
    history = history + [
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": "_Thinking..._"},
    ]
    yield history, "", trace_ids

    steps: list[str] = []
    async for event in stream_chat(user_message, previous, parse_coords(shared_location), session_id=session_id):
        if event["type"] == "step":
            steps.append(event["label"])
            history[-1] = {"role": "assistant", "content": "_" + " → ".join(steps) + "..._"}
        elif event["type"] == "answer":
            footer = f"\n\n<sub>route: {' · '.join(event['route'])} · {event['seconds']}s</sub>"
            history[-1] = {"role": "assistant", "content": (event["answer"] or "(no answer)") + footer}
            if event.get("trace_id"):
                trace_ids[len(history) - 1] = event["trace_id"]  # message index -> trace, for feedback
        yield history, "", trace_ids


def handle_feedback(data: gr.LikeData, trace_ids):
    """Send the 👍/👎 on an answer to Langfuse as a `user_feedback` score on that answer's trace."""
    index = data.index[0] if isinstance(data.index, list | tuple) else data.index
    trace_id = (trace_ids or {}).get(index)
    if trace_id and data.liked in (True, False):
        observability.score(trace_id, "user_feedback", 1.0 if data.liked else 0.0, data_type="BOOLEAN")


def build_demo() -> gr.Blocks:
    with gr.Blocks(title="Connoisseur Companion") as demo:
        gr.Markdown(
            "# Connoisseur Companion\n"
            "Restaurants anywhere (California guide + OpenStreetMap), recipes and reviews. "
            "Tell me where you are and what you feel like eating."
        )
        session_id = gr.State(lambda: uuid.uuid4().hex)  # one Langfuse session per browser tab
        trace_ids = gr.State({})
        chatbot = gr.Chatbot(height=520, feedback_options=("Like", "Dislike"))
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
        msg_input.submit(handle_chat, [msg_input, chatbot, location_box, session_id, trace_ids],
                         [chatbot, msg_input, trace_ids])
        chatbot.like(handle_feedback, [trace_ids], None)
    return demo


def main():
    parser = argparse.ArgumentParser(description="Connoisseur Companion developer UI")
    parser.add_argument("--share", action="store_true", help="create a public Gradio link")
    args = parser.parse_args()
    build_demo().launch(share=args.share, theme=gr.themes.Soft())


if __name__ == "__main__":
    main()
