"""Entry point used by the API and the dev UI: run the graph and stream progress events."""
import time
from collections.abc import AsyncIterator
from typing import Any

from connoisseur.agents.graph import get_graph
from connoisseur.log import get_logger
from connoisseur.schemas import Coordinates

log = get_logger(__name__)

# Human-readable progress labels for the UI
STEP_LABELS = {
    "profile": "Understanding your request",
    "locate": "Finding your location",
    "ask_location": "Asking where you are",
    "retrieve_local": "Searching the California guide",
    "web_search": "Searching OpenStreetMap and the web",
    "trend_analyst": "Trend analyst reviewing options",
    "style_expert": "Food style expert reviewing options",
    "nutrition_expert": "Nutrition expert reviewing options",
    "recommend": "Writing recommendations",
    "tool_agent": "Looking it up in the guide",
    "respond": "Answering",
}


def initial_state(message: str, history: list[dict] | None, coords: Coordinates | None) -> dict:
    return {
        "message": message,
        "history": history or [],
        "coords": coords.rounded() if coords else None,
        "route": [],
    }


async def stream_chat(
    message: str,
    history: list[dict] | None = None,
    coords: Coordinates | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield `step` events while the graph runs, then one `answer` event."""
    start = time.perf_counter()
    final: dict[str, Any] = {}
    async for update in get_graph().astream(initial_state(message, history, coords), stream_mode="updates"):
        for node, delta in update.items():
            if node == "experts":
                continue
            delta = delta or {}
            final.update(delta)
            yield {"type": "step", "node": node, "label": STEP_LABELS.get(node, node)}

    location = final.get("location")
    context = final.get("context")
    elapsed = round(time.perf_counter() - start, 2)
    log.info("chat_done", route=final.get("route"), seconds=elapsed)
    yield {
        "type": "answer",
        "answer": final.get("answer", ""),
        "cards": final.get("cards", []),
        "route": final.get("route", []),
        "intent": context.intent if context else None,
        "location": location.display_name if location else None,
        "seconds": elapsed,
    }


async def run_chat(message: str, history: list[dict] | None = None, coords: Coordinates | None = None) -> dict:
    """Non-streaming helper: return only the final answer event."""
    result: dict[str, Any] = {}
    async for event in stream_chat(message, history, coords):
        if event["type"] == "answer":
            result = event
    return result
