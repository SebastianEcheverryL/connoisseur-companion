import type { UIMessageChunk } from "ai";

import type { BackendEvent, ChatDataParts } from "./types";

type Chunk = UIMessageChunk<never, ChatDataParts>;

/**
 * Translate the backend's events into AI SDK UI message chunks:
 *  - each `step` updates one persistent `data-step` part (same id = reconciled, so the UI shows the list growing)
 *  - the `answer` becomes a text part plus a `data-result` part with cards, route and trace id
 *  - an `error` becomes an error chunk with a safe message
 */
export function* toUIChunks(event: BackendEvent, state: { steps: string[]; textId: string }): Generator<Chunk> {
  switch (event.type) {
    case "step":
      state.steps.push(event.label);
      yield { type: "data-step", id: "steps", data: { labels: [...state.steps] } };
      break;
    case "answer":
      yield { type: "text-start", id: state.textId };
      yield { type: "text-delta", id: state.textId, delta: event.answer || "…" };
      yield { type: "text-end", id: state.textId };
      yield {
        type: "data-result",
        id: "result",
        data: {
          cards: event.cards ?? [],
          route: event.route ?? [],
          traceId: event.trace_id ?? null,
          location: event.location ?? null,
          seconds: event.seconds ?? null,
        },
      };
      break;
    case "error":
      yield { type: "error", errorText: event.message || "Something went wrong." };
      break;
    case "done":
      break;
  }
}
