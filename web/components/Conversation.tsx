"use client";

import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";
import dynamic from "next/dynamic";
import { type FormEvent, type RefObject, useEffect, useEffectEvent, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import Cards from "@/components/Cards";
import Feedback from "@/components/Feedback";
import { PinIcon, SendIcon, StopIcon } from "@/components/icons";
import type { Geolocation } from "@/components/stores";
import { answerWithoutPickLists, isSpanish, shortPlace } from "@/lib/answer";
import { chatTitle } from "@/lib/chats";
import { messageText } from "@/lib/history";
import type { ChatDataParts, ChatMessage, Coordinates } from "@/lib/types";

// Leaflet touches `window`: load the map in the browser only
const PlacesMap = dynamic(() => import("@/components/PlacesMap"), { ssr: false });

const transport = new DefaultChatTransport<ChatMessage>({ api: "/api/chat" });

const EXAMPLES = [
  { label: "Restaurant · Español", text: "Un café vegano tranquilo en El Poblado, Medellín" },
  { label: "Near me", text: "Recomiéndame sushi cerca de mí" },
  { label: "Restaurant", text: "Find me a moody Italian spot in Pasadena" },
  { label: "Lookup", text: "Tell me about Sakura Garden and its review" },
  { label: "Recipe", text: "What should I cook tonight? Something with noodles" },
];

// Graph nodes that search for places or recipes: while they run, the answer shows placeholder cards
const SEARCH_NODES = new Set(["retrieve_local", "web_search", "trend_analyst", "style_expert", "nutrition_expert"]);
const SHORT_NODE: Record<string, string> = {
  profile: "profile",
  locate: "locate",
  ask_location: "ask location",
  retrieve_local: "guide",
  web_search: "web search",
  trend_analyst: "experts",
  style_expert: "experts",
  nutrition_expert: "experts",
  recommend: "recommend",
  tool_agent: "lookup",
  respond: "respond",
};
const INTENT_LABEL: Record<string, string> = { restaurant: "Restaurant", recipe: "Recipe", both: "Restaurant + recipe" };

type Props = {
  id: string;
  initialMessages: ChatMessage[];
  geo: Geolocation;
  /** Called with the full conversation each time an answer finishes (to keep it in the recent list). */
  onSettled: (messages: ChatMessage[]) => void;
};

export default function Conversation({ id, initialMessages, geo, onSettled }: Props) {
  const [input, setInput] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const { messages, sendMessage, status, error, stop } = useChat<ChatMessage>({
    id,
    messages: initialMessages,
    transport,
  });
  const busy = status === "submitted" || status === "streaming";

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, status]);

  // Persist once an answer has settled (not on every streamed chunk, and not just for opening an old chat)
  const settle = useEffectEvent(onSettled);
  useEffect(() => {
    if ((status === "ready" || status === "error") && messages.length > initialMessages.length) settle(messages);
  }, [status, messages, initialMessages.length]);

  function send(text: string, options: { location?: Coordinates | null; locationShared?: boolean } = {}) {
    const trimmed = text.trim();
    if (!trimmed || busy) return;
    sendMessage(
      { text: trimmed, metadata: options.locationShared ? { locationShared: true } : undefined },
      { body: { location: options.location ?? geo.location } },
    );
    setInput("");
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    send(input);
  }

  /** "Near me" without a position: share it, then ask the same question again with coordinates. */
  async function shareAndRetry() {
    const coords = await geo.request();
    const lastUser = [...messages].reverse().find((m) => m.role === "user");
    if (coords && lastUser) send(messageText(lastUser), { location: coords, locationShared: true });
  }

  const empty = messages.length === 0;
  const lastResult = findResult(messages.at(-1));

  return (
    <main className="flex min-h-0 flex-1 flex-col">
      {!empty && (
        <header className="flex h-16 flex-none items-center gap-3 border-b border-border px-4 md:px-10">
          <h1 className="min-w-0 truncate font-display text-[19px]">{chatTitle(messages)}</h1>
          <span className="ml-auto flex-none text-[13px] text-muted">
            {busy ? (
              <span className="flex items-center gap-2 font-semibold text-accent">
                <span className="skeleton h-2 w-2 rounded-full bg-accent" />
                Searching
              </span>
            ) : (
              lastResult && resultSummary(lastResult)
            )}
          </span>
        </header>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto">
        {empty ? (
          <Welcome onPick={(text) => send(text)} />
        ) : (
          <div className="mx-auto flex max-w-5xl flex-col gap-[22px] px-4 pt-7 pb-4 md:px-10">
            {messages.map((message, i) =>
              message.role === "user" ? (
                <UserTurn key={message.id} message={message} next={messages[i + 1]} previous={messages[i - 1]} />
              ) : (
                <AssistantTurn
                  key={message.id}
                  message={message}
                  streaming={busy && i === messages.length - 1}
                  isLast={i === messages.length - 1}
                  onShareLocation={shareAndRetry}
                  onTypeCity={() => inputRef.current?.focus()}
                  geoStatus={geo.status}
                />
              ),
            )}
            {status === "submitted" && <Thinking />}
            {error && (
              <p role="alert" className="rounded-2xl bg-danger-soft px-4 py-3 text-sm text-danger-text">
                {error.message || "Something went wrong. Please try again."}
              </p>
            )}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      <Composer
        variant={empty ? "welcome" : "followup"}
        input={input}
        setInput={setInput}
        onSubmit={onSubmit}
        busy={busy}
        onStop={stop}
        geo={geo}
        inputRef={inputRef}
      />
    </main>
  );
}

/* ---------------------------------------------------------------- Welcome */

function Welcome({ onPick }: { onPick: (text: string) => void }) {
  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-8 px-4 pt-10 md:px-[88px] md:pt-[72px]">
      <div className="flex max-w-[640px] flex-col gap-3">
        <span className="self-start rounded-full bg-accent-soft px-3 py-[5px] text-xs font-semibold text-accent-text">
          Restaurants · Recipes · Reviews
        </span>
        <h2 className="font-display text-[34px] leading-[1.08] tracking-[-.01em] md:text-5xl">
          What do you feel like eating?
        </h2>
        <p className="text-[17px] leading-normal text-pretty text-muted">
          Restaurants anywhere (California guide + OpenStreetMap), recipes and reviews. Tell me where you are and what
          you feel like eating.
        </p>
      </div>
      <ul className="grid max-w-[960px] gap-3.5 sm:grid-cols-2 lg:grid-cols-3">
        {EXAMPLES.map((example) => (
          <li key={example.text}>
            <button
              onClick={() => onPick(example.text)}
              className="flex h-full w-full flex-col gap-2.5 rounded-[20px] border border-border bg-surface p-[18px] text-left transition-colors hover:border-accent"
            >
              <span className="text-[11px] font-semibold uppercase tracking-[.08em] text-accent">{example.label}</span>
              <span className="text-[15px] leading-[1.4]">{example.text}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

/* ---------------------------------------------------------------- Composer */

type ComposerProps = {
  variant: "welcome" | "followup";
  input: string;
  setInput: (value: string) => void;
  onSubmit: (event: FormEvent) => void;
  busy: boolean;
  onStop: () => void;
  geo: Geolocation;
  inputRef: RefObject<HTMLInputElement | null>;
};

function Composer({ variant, input, setInput, onSubmit, busy, onStop, geo, inputRef }: ComposerProps) {
  const field = (
    <input
      ref={inputRef}
      value={input}
      onChange={(e) => setInput(e.target.value)}
      maxLength={2000}
      placeholder={variant === "welcome" ? "Ask for a place or a recipe…" : "Ask a follow-up…"}
      aria-label="Your message"
      className="min-w-0 flex-1 bg-transparent text-base outline-none placeholder:text-muted"
    />
  );
  const action = busy ? (
    <button
      type="button"
      onClick={onStop}
      aria-label="Stop"
      className="grid h-[42px] w-[42px] flex-none place-items-center rounded-full bg-surface-2 text-muted"
    >
      <StopIcon />
    </button>
  ) : (
    <button
      type="submit"
      disabled={!input.trim()}
      aria-label="Send"
      className="grid h-11 w-11 flex-none place-items-center rounded-full bg-accent text-on-accent disabled:opacity-40"
    >
      <SendIcon size={18} />
    </button>
  );
  const toggleLocation = () => (geo.location ? geo.stop() : void geo.request());

  if (variant === "welcome") {
    const note = geo.location
      ? "Shared, rounded to ~1 km"
      : geo.status === "asking"
        ? "Waiting for permission…"
        : geo.status === "denied"
          ? "Location not available. Type your city instead."
          : "Not shared: the assistant will ask where you are";
    return (
      <form onSubmit={onSubmit} className="mx-auto flex w-full max-w-5xl flex-col gap-2.5 px-4 pt-5 pb-7 md:px-[88px]">
        <div className="flex flex-col gap-3.5 rounded-[28px] border-[1.5px] border-border bg-background py-4 pr-4 pl-[22px] shadow-soft focus-within:border-accent">
          {field}
          <div className="flex items-center gap-2.5">
            <button
              type="button"
              onClick={toggleLocation}
              disabled={geo.status === "asking"}
              aria-pressed={!!geo.location}
              className={`flex h-9 flex-none items-center gap-[7px] rounded-full border px-3.5 text-[13px] font-semibold ${
                geo.location
                  ? "border-accent-soft bg-accent-soft text-accent-text"
                  : "border-border bg-surface text-foreground"
              }`}
            >
              <PinIcon size={15} />
              {geo.location ? "Sharing location" : "Use my location"}
            </button>
            <span className="hidden min-w-0 truncate text-[13px] text-muted sm:inline">{note}</span>
            <span className="ml-auto">{action}</span>
          </div>
        </div>
        <span className="pl-[22px] text-xs text-muted">
          Shared location is rounded to ~1 km before it leaves your browser.
        </span>
      </form>
    );
  }

  return (
    <form onSubmit={onSubmit} className="mx-auto w-full max-w-5xl px-4 pt-4 pb-6 md:px-10">
      <div className="flex items-center gap-2.5 rounded-full border-[1.5px] border-border bg-background py-2 pr-2 pl-[22px] shadow-soft focus-within:border-accent">
        {field}
        {!busy && (
          <button
            type="button"
            onClick={toggleLocation}
            disabled={geo.status === "asking"}
            aria-pressed={!!geo.location}
            aria-label={geo.location ? "Stop sharing my location" : "Use my location"}
            title={geo.location ? "Sharing your location (rounded to ~1 km)" : "Use my location"}
            className={`grid h-9 w-9 flex-none place-items-center rounded-full border ${
              geo.location ? "border-accent-soft bg-accent-soft text-accent-text" : "border-border bg-surface text-muted"
            }`}
          >
            <PinIcon size={16} />
          </button>
        )}
        {action}
      </div>
    </form>
  );
}

/* ---------------------------------------------------------------- Turns */

function UserTurn({ message, next, previous }: { message: ChatMessage; next?: ChatMessage; previous?: ChatMessage }) {
  if (message.metadata?.locationShared) {
    const spanish = isSpanish(findResult(previous)?.language);
    const place = findResult(next)?.location;
    return (
      <div className="flex items-center gap-2 self-end rounded-full bg-accent-soft px-4 py-2.5 text-[13px] font-semibold text-accent-text">
        <PinIcon size={14} strokeWidth={2.75} />
        {spanish ? "Ubicación compartida" : "Location shared"}
        {place ? ` · ${shortPlace(place)}` : ""}
      </div>
    );
  }
  return (
    <p className="max-w-[min(520px,85%)] self-end rounded-[22px_22px_6px_22px] bg-accent px-[18px] py-3 text-[15px] whitespace-pre-wrap text-on-accent">
      {messageText(message)}
    </p>
  );
}

function Avatar() {
  return (
    <div
      className="grid h-[34px] w-[34px] flex-none place-items-center rounded-full bg-accent-soft font-display text-[17px] text-accent-text"
      aria-hidden="true"
    >
      C
    </div>
  );
}

function Thinking() {
  return (
    <div className="flex items-center gap-3.5">
      <Avatar />
      <span className="skeleton text-sm text-muted">Thinking…</span>
    </div>
  );
}

type AssistantProps = {
  message: ChatMessage;
  streaming: boolean;
  isLast: boolean;
  onShareLocation: () => void;
  onTypeCity: () => void;
  geoStatus: Geolocation["status"];
};

function AssistantTurn({ message, streaming, isLast, onShareLocation, onTypeCity, geoStatus }: AssistantProps) {
  let steps: ChatDataParts["step"] = { labels: [], nodes: [] };
  let result: ChatDataParts["result"] | null = null;
  let text = "";
  for (const part of message.parts) {
    if (part.type === "data-step") steps = { labels: part.data.labels, nodes: part.data.nodes ?? [] };
    else if (part.type === "data-result") result = part.data;
    else if (part.type === "text") text += part.text;
  }
  const cards = result?.cards ?? [];
  const places = cards.filter((c) => c.type === "restaurant" && c.coordinates);
  const searching = streaming && !result && steps.nodes.some((node) => SEARCH_NODES.has(node));
  const asksLocation = !!result?.route.includes("ask_location");
  const spanish = isSpanish(result?.language);
  const shownText = cards.length > 0 ? answerWithoutPickLists(text) : text;

  return (
    <article className="flex items-start gap-3.5">
      <Avatar />
      <div className="flex min-w-0 flex-1 flex-col gap-3.5">
        {steps.labels.length > 0 && <Steps labels={steps.labels} streaming={streaming} />}

        {result && <Understood result={result} />}

        {shownText &&
          (cards.length > 0 ? (
            <Markdown className="text-base leading-normal">{shownText}</Markdown>
          ) : (
            <div className="flex max-w-[620px] flex-col gap-3.5 self-start rounded-[6px_22px_22px_22px] border border-border bg-surface px-5 py-[18px]">
              <Markdown className="text-[15px] leading-normal">{shownText}</Markdown>
              {asksLocation && isLast && (
                <>
                  <div className="flex flex-wrap gap-2">
                    <button
                      onClick={onShareLocation}
                      disabled={geoStatus === "asking"}
                      className="flex h-[38px] items-center gap-[7px] rounded-full bg-accent px-4 text-[13px] font-semibold text-on-accent disabled:opacity-60"
                    >
                      <PinIcon size={15} />
                      {spanish ? "Compartir mi ubicación" : "Share my location"}
                    </button>
                    <button
                      onClick={onTypeCity}
                      className="h-[38px] rounded-full border border-border bg-background px-4 text-[13px] font-semibold"
                    >
                      {spanish ? "Escribir una ciudad" : "Type a city"}
                    </button>
                  </div>
                  <span className="text-xs text-muted">
                    {geoStatus === "denied"
                      ? spanish
                        ? "No se pudo obtener tu ubicación. Escribe tu ciudad o barrio."
                        : "Your location isn't available. Type your city or neighbourhood instead."
                      : spanish
                        ? "Tu ubicación se redondea a ~1 km antes de salir del navegador."
                        : "Your location is rounded to ~1 km before it leaves your browser."}
                  </span>
                </>
              )}
            </div>
          ))}

        {searching && <SkeletonCards />}
        {cards.length > 0 && <Cards cards={cards} />}
        {places.length > 0 && <PlacesMap places={places} />}

        {result && (
          <div className="flex flex-wrap items-center gap-2">
            {result.traceId && <Feedback traceId={result.traceId} />}
            <span className="ml-1.5 text-xs text-muted">{routeSummary(steps.nodes, result.seconds)}</span>
          </div>
        )}
      </div>
    </article>
  );
}

function Markdown({ children, className }: { children: string; className?: string }) {
  return (
    <div className={`prose-answer text-pretty ${className ?? ""}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{ a: (props) => <a {...props} target="_blank" rel="noopener noreferrer" /> }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}

/** Agent progress as chips: done steps get a check, the running one is highlighted. */
function Steps({ labels, streaming }: { labels: string[]; streaming: boolean }) {
  return (
    <ol aria-label="Agent steps" className="flex flex-wrap items-center gap-1.5 text-xs text-muted">
      {labels.map((label, i) => {
        const running = streaming && i === labels.length - 1;
        return (
          <li key={i} className="flex items-center gap-1.5">
            {i > 0 && <span aria-hidden="true">→</span>}
            <span
              className={`rounded-full border px-2.5 py-1 ${
                running ? "border-accent bg-accent-soft font-semibold text-accent-text" : "border-border bg-surface"
              }`}
            >
              {running ? "● " : "✓ "}
              {label}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

/** What the profile agent understood: intent, place and preferences. */
function Understood({ result }: { result: ChatDataParts["result"] }) {
  const chips = [
    result.intent ? INTENT_LABEL[result.intent] : null,
    result.location ? shortPlace(result.location) : null,
    ...(result.understood ?? []),
  ].filter((chip): chip is string => !!chip);
  if (chips.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="mr-1 text-xs text-muted">Understood</span>
      {chips.map((chip) => (
        <span key={chip} className="rounded-full bg-accent-soft px-[11px] py-1 text-xs font-semibold text-accent-text">
          {chip}
        </span>
      ))}
    </div>
  );
}

function SkeletonCards() {
  return (
    <div aria-hidden="true" className="grid gap-3.5 sm:grid-cols-2 lg:grid-cols-3">
      {[75, 65, 70].map((width) => (
        <div key={width} className="skeleton flex flex-col gap-2.5 rounded-[20px] border border-border bg-surface p-4">
          <div className="h-3 w-2/5 rounded-full bg-surface-2" />
          <div className="h-[18px] rounded-full bg-surface-2" style={{ width: `${width}%` }} />
          <div className="h-2.5 w-[85%] rounded-full bg-surface-2" />
          <div className="h-2.5 w-[55%] rounded-full bg-surface-2" />
        </div>
      ))}
    </div>
  );
}

/* ---------------------------------------------------------------- helpers */

function findResult(message: ChatMessage | undefined): ChatDataParts["result"] | null {
  for (const part of message?.parts ?? []) if (part.type === "data-result") return part.data;
  return null;
}

function resultSummary(result: ChatDataParts["result"]): string {
  const restaurants = result.cards.filter((c) => c.type === "restaurant").length;
  const recipes = result.cards.filter((c) => c.type === "recipe").length;
  const parts = [
    restaurants ? `${restaurants} ${restaurants === 1 ? "pick" : "picks"}` : null,
    recipes ? `${recipes} ${recipes === 1 ? "recipe" : "recipes"}` : null,
    result.seconds != null ? `${Math.round(result.seconds)}s` : null,
  ];
  return parts.filter(Boolean).join(" · ");
}

function routeSummary(nodes: string[], seconds: number | null): string {
  const names = [...new Set(nodes.map((node) => SHORT_NODE[node]).filter(Boolean))];
  const time = seconds != null ? `${Math.round(seconds)}s` : null;
  if (names.length === 0) return time ?? "";
  return `route: ${[...names, time].filter(Boolean).join(" · ")}`;
}
