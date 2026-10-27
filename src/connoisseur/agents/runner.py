"""Entry point used by the API and the dev UI: run the graph and stream progress events."""
import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

from connoisseur import memory
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
    "recall": "Looking up a place from earlier",
    "respond": "Answering",
}


def understood(context) -> list[str]:
    """Preferences the profile agent extracted, shown back to the user as chips ("Understood: Italian, moody")."""
    if context is None:
        return []
    items = [*context.cuisines, *context.dietary, context.vibe, context.budget, context.occasion]
    chips, seen = [], set()
    for item in items:
        if item and item.lower() not in seen:  # "casual" as vibe and as occasion: one chip
            seen.add(item.lower())
            chips.append(item)
    return chips[:8]


def initial_state(message: str, history: list[dict] | None, coords: Coordinates | None,
                  conversation: memory.ConversationState | None = None) -> dict:
    conversation = conversation or memory.ConversationState()
    return {
        "message": message,
        "history": history or [],
        "coords": coords.rounded() if coords else None,
        "memory": conversation.profile,
        "last_cards": conversation.last_cards,
        "home_location": conversation.home_location,
        "route": [],
    }


async def _load_memory(user_id: str | None, session_id: str | None) -> tuple[memory.ConversationState, bool]:
    try:
        return await asyncio.to_thread(memory.load, user_id, session_id)
    except Exception as exc:  # noqa: BLE001 - a memory outage must not take the chat down
        log.warning("memory_load_failed", error=str(exc)[:200])
        return memory.ConversationState(), False


async def _save_memory(user_id: str | None, session_id: str | None, conversation: memory.ConversationState,
                       final: dict) -> memory.ConversationState:
    """Fold this turn into the conversation state and store it. Blocked or failed turns change nothing."""
    if "memory" not in final:
        return conversation
    updated = memory.ConversationState(
        profile=final["memory"],
        # Keep the previous picks when this answer has none (a lookup about "the second one")
        last_cards=memory.card_refs(final.get("cards") or []) or conversation.last_cards,
        home_location=conversation.home_location,
    )
    try:
        await asyncio.to_thread(memory.save, user_id, session_id, updated, final.get("named_location"))
    except Exception as exc:  # noqa: BLE001
        log.warning("memory_save_failed", error=str(exc)[:200])
    return updated


async def stream_chat(
    message: str,
    history: list[dict] | None = None,
    coords: Coordinates | None = None,
    *,
    session_id: str | None = None,
    user_id: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield `step` events while the graph runs, then one `answer` event (with its trace id).

    With a `session_id`, the conversation remembers its profile and last recommendations between
    messages; with a signed-in `user_id` ("user:..."), a new chat starts from the user's saved memory.
    """
    start = time.perf_counter()
    final: dict[str, Any] = {}
    conversation, from_saved = await _load_memory(user_id, session_id)
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
                    initial_state(message, history, coords, conversation), config=config, stream_mode="updates"
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

        conversation = await _save_memory(user_id, session_id, conversation, final)
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
            "memory": memory.summary(conversation, from_saved, bool(final.get("used_home"))),
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
