import type { UIMessage } from "ai";

/** A restaurant or recipe card, exactly as the FastAPI backend returns it. */
export type Card = {
  type: "restaurant" | "recipe";
  id: string;
  name: string;
  reasoning?: string;
  source?: "dataset" | "osm" | "web";
  location?: string;
  cuisine?: string;
  rating?: number;
  rating_source?: string;
  review_count?: number;
  review_url?: string;
  price_range?: number;
  url?: string;
  coordinates?: { lat: number; lon: number };
};

/** Events emitted by the backend's POST /chat (server-sent events). */
export type BackendEvent =
  | { type: "step"; node: string; label: string }
  | {
      type: "answer";
      answer: string;
      cards: Card[];
      route: string[];
      intent?: string | null;
      location?: string | null;
      seconds?: number;
      trace_id?: string | null;
    }
  | { type: "error"; message: string }
  | { type: "done" };

/** Custom data parts streamed to the UI next to the assistant's text. */
export type ChatDataParts = {
  step: { labels: string[] };
  result: {
    cards: Card[];
    route: string[];
    traceId: string | null;
    location: string | null;
    seconds: number | null;
  };
};

export type ChatMessage = UIMessage<never, ChatDataParts>;

export type Coordinates = { lat: number; lon: number };
