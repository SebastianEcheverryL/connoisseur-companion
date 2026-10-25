import type { ChatMessage } from "./types";

const MAX_TURNS = 10;
const MAX_CHARS = 2000;

/** Plain text of a UI message (text parts only: steps and cards are UI-only data). */
export function messageText(message: ChatMessage): string {
  return message.parts
    .filter((part) => part.type === "text")
    .map((part) => (part as { text: string }).text)
    .join("\n")
    .trim();
}

/**
 * Split UI messages into the backend's contract: the latest user message, plus a short history of
 * previous turns. Limits mirror the API's validation (20 turns, 4000 chars per turn).
 */
export function toBackendRequest(messages: ChatMessage[]): {
  message: string;
  history: { role: "user" | "assistant"; content: string }[];
} {
  const lastUser = [...messages].reverse().find((m) => m.role === "user");
  const message = lastUser ? messageText(lastUser).slice(0, MAX_CHARS) : "";
  const previous = messages.slice(0, lastUser ? messages.lastIndexOf(lastUser) : messages.length);
  const history = previous
    .filter((m) => m.role === "user" || m.role === "assistant")
    .map((m) => ({ role: m.role as "user" | "assistant", content: messageText(m).slice(0, 4000) }))
    .filter((turn) => turn.content)
    .slice(-MAX_TURNS);
  return { message, history };
}
