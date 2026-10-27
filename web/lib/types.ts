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
  // Recipes: ingredients and the similarity of each modality behind the fused score
  ingredients?: string[];
  score?: number;
  text_score?: number;
  image_score?: number;
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
      language?: string | null;
      understood?: string[];
      location?: string | null;
      memory?: Memory | null;
      seconds?: number;
      trace_id?: string | null;
    }
  | { type: "error"; message: string }
  | { type: "done" };

/** What the backend remembers for this conversation (and, for signed-in users, across chats). */
export type Memory = {
  location: string | null;
  dietary: string[];
  allergies: string[];
  /** True when something came from an earlier chat (saved diet, allergies or city). */
  from_saved: boolean;
};

/** Custom data parts streamed to the UI next to the assistant's text. */
export type ChatDataParts = {
  step: { labels: string[]; nodes: string[] };
  result: {
    cards: Card[];
    route: string[];
    traceId: string | null;
    intent: string | null;
    language: string | null;
    understood: string[];
    location: string | null;
    memory: Memory | null;
    seconds: number | null;
  };
};

/** Per-message metadata set by the UI: a user turn re-sent after sharing the location shows as a pill. */
export type ChatMetadata = { locationShared?: boolean };

export type ChatMessage = UIMessage<ChatMetadata, ChatDataParts>;

export type Coordinates = { lat: number; lon: number };
