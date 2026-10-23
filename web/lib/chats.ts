import type { ChatMessage } from "./types";

/**
 * Recent conversations, kept in this browser only (localStorage): a per-viewer convenience,
 * never sent anywhere. Every access is guarded because storage can be blocked or full.
 */
export type StoredChat = { id: string; title: string; messages: ChatMessage[]; updatedAt: number };

const KEY = "connoisseur:chats:v1";
const MAX_CHATS = 20;

export function loadChats(): StoredChat[] {
  try {
    const raw = window.localStorage.getItem(KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (c): c is StoredChat =>
        !!c && typeof c.id === "string" && typeof c.title === "string" && Array.isArray(c.messages),
    );
  } catch {
    return [];
  }
}

export function saveChats(chats: StoredChat[]): void {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(chats.slice(0, MAX_CHATS)));
  } catch {
    // Storage blocked or full: history just isn't kept
  }
}

/** Insert or update one chat and keep the list newest first. */
export function upsertChat(chats: StoredChat[], chat: StoredChat): StoredChat[] {
  return [chat, ...chats.filter((c) => c.id !== chat.id)].slice(0, MAX_CHATS);
}

/** Title for the sidebar: the first user message, shortened. */
export function chatTitle(messages: ChatMessage[]): string {
  const first = messages.find((m) => m.role === "user");
  const text = first?.parts.map((p) => (p.type === "text" ? p.text : "")).join(" ").trim() ?? "";
  if (!text) return "New chat";
  return text.length > 42 ? `${text.slice(0, 40).trimEnd()}…` : text;
}
