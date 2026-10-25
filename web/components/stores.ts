"use client";

import { useCallback, useState, useSyncExternalStore } from "react";

import { chatTitle, loadChats, saveChats, type StoredChat, upsertChat } from "@/lib/chats";
import { roundCoordinates } from "@/lib/geo";
import type { ChatMessage, Coordinates } from "@/lib/types";

/* ---------- Recent chats (localStorage, read through useSyncExternalStore so SSR renders an empty list) */

const NO_CHATS: StoredChat[] = [];
let chatsCache: StoredChat[] | null = null;
const chatListeners = new Set<() => void>();

function chatsSnapshot(): StoredChat[] {
  chatsCache ??= loadChats();
  return chatsCache;
}

function subscribeChats(listener: () => void) {
  chatListeners.add(listener);
  return () => chatListeners.delete(listener);
}

export function useChats(): StoredChat[] {
  return useSyncExternalStore(subscribeChats, chatsSnapshot, () => NO_CHATS);
}

/** Save a conversation (no-op while it is empty). */
export function rememberChat(id: string, messages: ChatMessage[]): void {
  if (messages.length === 0) return;
  chatsCache = upsertChat(chatsSnapshot(), { id, title: chatTitle(messages), messages, updatedAt: Date.now() });
  saveChats(chatsCache);
  chatListeners.forEach((listener) => listener());
}

/* ---------- Theme: explicit choice in localStorage, otherwise the system setting */

export type Theme = "light" | "dark";
const THEME_KEY = "connoisseur:theme";
const themeListeners = new Set<() => void>();

function themeSnapshot(): Theme {
  const explicit = document.documentElement.dataset.theme;
  if (explicit === "light" || explicit === "dark") return explicit;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function subscribeTheme(listener: () => void) {
  themeListeners.add(listener);
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  media.addEventListener("change", listener);
  return () => {
    themeListeners.delete(listener);
    media.removeEventListener("change", listener);
  };
}

export function useTheme(): [Theme, (theme: Theme) => void] {
  const theme = useSyncExternalStore(subscribeTheme, themeSnapshot, () => "light" as Theme);
  const setTheme = useCallback((next: Theme) => {
    document.documentElement.dataset.theme = next;
    try {
      window.localStorage.setItem(THEME_KEY, next);
    } catch {
      // Not persisted: the choice still applies to this page
    }
    themeListeners.forEach((listener) => listener());
  }, []);
  return [theme, setTheme];
}

/* ---------- Optional browser geolocation, rounded to ~1 km before it is stored or sent */

export type GeoStatus = "idle" | "asking" | "denied";

export function useGeolocation() {
  const [location, setLocation] = useState<Coordinates | null>(null);
  const [status, setStatus] = useState<GeoStatus>("idle");

  const request = useCallback(
    () =>
      new Promise<Coordinates | null>((resolve) => {
        if (!navigator.geolocation) {
          setStatus("denied");
          return resolve(null);
        }
        setStatus("asking");
        navigator.geolocation.getCurrentPosition(
          (pos) => {
            const rounded = roundCoordinates(pos.coords.latitude, pos.coords.longitude);
            setLocation(rounded);
            setStatus("idle");
            resolve(rounded);
          },
          () => {
            setStatus("denied");
            resolve(null);
          },
          { timeout: 10_000, maximumAge: 600_000 },
        );
      }),
    [],
  );

  const stop = useCallback(() => {
    setLocation(null);
    setStatus("idle");
  }, []);

  return { location, status, request, stop };
}

export type Geolocation = ReturnType<typeof useGeolocation>;
