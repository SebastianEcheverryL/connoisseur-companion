"""Entry point used by the API and the dev UI: run the graph and stream progress events."""
import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

from connoisseur.agents.graph import get_graph
from connoisseur.agents.nodes import guess_language, t_lang
from connoisseur.log import get_logger
from connoisseur.observability import langchain_callbacks, trace_request
from connoisseur.schemas import Coordinates
from connoisseur.settings import get_settings

log = get_logger(__name__)

# Human-readable progress labels for the UI
STEP_LABELS = {
    "input_guard": "Checking your message",
    "output_guard": "Checking the answer",
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


def understood(context) -> list[str]:
    """Preferences the profile agent extracted, shown back to the user as chips ("Understood: Italian, moody")."""
    if context is None:
        return []
    items = [*context.cuisines, *context.dietary, context.vibe, context.budget, context.occasion]
    return [item for item in items if item][:8]


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
    *,
    session_id: str | None = None,
    user_id: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield `step` events while the graph runs, then one `answer` event (with its trace id)."""
    start = time.perf_counter()
    final: dict[str, Any] = {}
    with trace_request(
        "chat",
        input={"message": message, "history_turns": len(history or []), "shared_location": coords is not None},
        user_id=user_id,
        session_id=session_id,
        tags=[get_settings().llm_provider],
    ) as trace:
        config = {"callbacks": langchain_callbacks(), "run_name": "recommendation_graph"}
        try:
            # A hard ceiling per request: slow models or hung tools must not hold resources forever
            async with asyncio.timeout(get_settings().request_timeout_s):
                async for update in get_graph().astream(
                    initial_state(message, history, coords), config=config, stream_mode="updates"
                ):
                    for node, delta in update.items():
                        if node == "experts":
                            continue
                        final.update(delta or {})
                        yield {"type": "step", "node": node, "label": STEP_LABELS.get(node, node)}
        except TimeoutError:
            log.warning("request_timeout", seconds=get_settings().request_timeout_s)
            final.update({"answer": t_lang(guess_language(message), "timeout"), "cards": [],
                          "route": [*final.get("route", []), "timeout"]})

        location = final.get("location")
        context = final.get("context")
        elapsed = round(time.perf_counter() - start, 2)
        answer = {
            "type": "answer",
            "answer": final.get("answer", ""),
            "cards": final.get("cards", []),
            "route": final.get("route", []),
            "intent": context.intent if context else None,
            "language": context.language if context else None,
            "understood": understood(context),
            "location": location.display_name if location else None,
            "seconds": elapsed,
            "trace_id": trace.trace_id,
            # Internal: what the answering agent saw. Used by evals; the API strips it before responding.
            "evidence": final.get("evidence", []),
        }
        trace.finish(
            output={"answer": answer["answer"], "cards": len(answer["cards"])},
            route=answer["route"],
            intent=answer["intent"],
            seconds=elapsed,
        )
    log.info("chat_done", route=answer["route"], seconds=elapsed, trace_id=trace.trace_id)
    yield answer


async def run_chat(message: str, history: list[dict] | None = None, coords: Coordinates | None = None,
                   **kwargs: Any) -> dict:
    """Non-streaming helper: return only the final answer event."""
    result: dict[str, Any] = {}
    async for event in stream_chat(message, history, coords, **kwargs):
        if event["type"] == "answer":
            result = event
    return result
