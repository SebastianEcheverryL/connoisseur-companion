"use client";

import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";
import dynamic from "next/dynamic";
import { type FormEvent, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import Cards from "@/components/Cards";
import Feedback from "@/components/Feedback";
import LocationButton from "@/components/LocationButton";
import type { ChatDataParts, ChatMessage, Coordinates } from "@/lib/types";

// Leaflet touches `window`: load the map in the browser only
const PlacesMap = dynamic(() => import("@/components/PlacesMap"), { ssr: false });

const EXAMPLES = [
  "Recomiéndame sushi cerca de mí",
  "Un café vegano tranquilo en El Poblado, Medellín",
  "Find me a moody Italian spot in Pasadena",
  "Tell me about Sakura Garden",
  "Something with chickpeas I can cook tonight",
];

export default function Chat() {
  const [input, setInput] = useState("");
  const [location, setLocation] = useState<Coordinates | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const { messages, sendMessage, status, error, stop } = useChat<ChatMessage>({
    transport: new DefaultChatTransport({ api: "/api/chat" }),
  });
  const busy = status === "submitted" || status === "streaming";

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, status]);

  function send(text: string) {
    const trimmed = text.trim();
    if (!trimmed || busy) return;
    sendMessage({ text: trimmed }, { body: { location } });
    setInput("");
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    send(input);
  }

  return (
    <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col px-4">
      <div className="flex-1 space-y-6 py-6">
        {messages.length === 0 && (
          <section className="space-y-4 py-8">
            <h2 className="text-xl font-semibold">What do you feel like eating?</h2>
            <p className="text-sm text-muted">
              Ask for restaurants in any city (or share your location), recipes for what you want to cook, or a
              specific place from the California guide. Every pick shows where it came from.
            </p>
            <div className="flex flex-wrap gap-2">
              {EXAMPLES.map((example) => (
                <button
                  key={example}
                  onClick={() => send(example)}
                  className="rounded-full border border-border bg-surface px-3 py-1.5 text-sm hover:bg-accent-soft"
                >
                  {example}
                </button>
              ))}
            </div>
          </section>
        )}

        {messages.map((message) =>
          message.role === "user" ? (
            <UserBubble key={message.id} message={message} />
          ) : (
            <AssistantMessage key={message.id} message={message} streaming={busy && message === messages.at(-1)} />
          ),
        )}

        {status === "submitted" && <p className="text-sm text-muted">Sending…</p>}
        {error && (
          <p role="alert" className="rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-200">
            {error.message || "Something went wrong. Please try again."}
          </p>
        )}
        <div ref={bottomRef} />
      </div>

      <form onSubmit={onSubmit} className="sticky bottom-0 space-y-2 border-t border-border bg-background py-3">
        <LocationButton value={location} onChange={setLocation} />
        <div className="flex gap-2">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            maxLength={2000}
            placeholder="Ask for a place or a recipe…"
            aria-label="Your message"
            className="min-w-0 flex-1 rounded-lg border border-border bg-surface px-3 py-2 text-sm outline-none focus:border-accent"
          />
          {busy ? (
            <button type="button" onClick={stop} className="rounded-lg border border-border px-4 py-2 text-sm">
              Stop
            </button>
          ) : (
            <button
              type="submit"
              disabled={!input.trim()}
              className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
            >
              Send
            </button>
          )}
        </div>
      </form>
    </main>
  );
}

function UserBubble({ message }: { message: ChatMessage }) {
  const text = message.parts.map((p) => (p.type === "text" ? p.text : "")).join("");
  return (
    <div className="flex justify-end">
      <p className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-accent px-4 py-2 text-sm text-white">
        {text}
      </p>
    </div>
  );
}

function AssistantMessage({ message, streaming }: { message: ChatMessage; streaming: boolean }) {
  let steps: string[] = [];
  let result: ChatDataParts["result"] | null = null;
  let text = "";
  for (const part of message.parts) {
    if (part.type === "data-step") steps = part.data.labels;
    else if (part.type === "data-result") result = part.data;
    else if (part.type === "text") text += part.text;
  }
  const places = (result?.cards ?? []).filter((c) => c.type === "restaurant" && c.coordinates);

  return (
    <article className="space-y-3">
      {steps.length > 0 && (
        <details open={streaming} className="text-xs text-muted">
          <summary className="cursor-pointer select-none">
            {streaming ? `${steps.at(-1)}…` : `${steps.length} steps${result?.seconds ? ` · ${result.seconds}s` : ""}`}
          </summary>
          <ol className="mt-1 ml-4 list-decimal space-y-0.5">
            {steps.map((label, i) => (
              <li key={i}>{label}</li>
            ))}
          </ol>
        </details>
      )}
      {text && (
        <div className="prose-answer rounded-2xl rounded-bl-sm border border-border bg-surface px-4 py-3 text-sm leading-relaxed">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{ a: (props) => <a {...props} target="_blank" rel="noopener noreferrer" /> }}
          >
            {text}
          </ReactMarkdown>
        </div>
      )}
      {result && result.cards.length > 0 && <Cards cards={result.cards} />}
      {places.length > 0 && <PlacesMap places={places} />}
      {result?.traceId && <Feedback traceId={result.traceId} />}
    </article>
  );
}
