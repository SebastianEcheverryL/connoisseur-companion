import { describe, expect, it } from "vitest";

import { toUIChunks } from "./adapter";
import { answerWithoutPickLists, isSpanish, shortPlace } from "./answer";
import { chatTitle, upsertChat } from "./chats";
import { parseCoordinates, roundCoordinates } from "./geo";
import { toBackendRequest } from "./history";
import { memoryChips, toSessionId } from "./memory";
import { parseSSE } from "./sse";
import type { BackendEvent, ChatMessage } from "./types";

function streamOf(...chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

async function collect(stream: ReadableStream<Uint8Array>): Promise<BackendEvent[]> {
  const events: BackendEvent[] = [];
  for await (const event of parseSSE(stream)) events.push(event);
  return events;
}

describe("parseSSE", () => {
  it("parses events split across network chunks", async () => {
    const events = await collect(
      streamOf(
        'event: step\ndata: {"type":"step","node":"profile","label":"Understanding"}\n\nevent: ans',
        'wer\ndata: {"type":"answer","answer":"Hola","cards":[],"route":["intent:x"]}\n\n',
        "event: done\ndata: {}\n\n",
      ),
    );
    expect(events.map((e) => e.type)).toEqual(["step", "answer", "done"]);
  });

  it("ignores comments, blank keep-alives and malformed data", async () => {
    const events = await collect(streamOf(": keep-alive\n\n", "data: {not json\n\n", 'data: {"type":"step","node":"a","label":"A"}\r\n\r\n'));
    expect(events).toEqual([{ type: "step", node: "a", label: "A" }]);
  });
});

describe("toUIChunks", () => {
  it("accumulates steps into one reconciled data part and maps the answer", () => {
    const state = { steps: [] as string[], nodes: [] as string[], textId: "t1" };
    const first = [...toUIChunks({ type: "step", node: "profile", label: "Understanding" }, state)];
    const second = [...toUIChunks({ type: "step", node: "locate", label: "Locating" }, state)];
    expect(first[0]).toEqual({ type: "data-step", id: "steps", data: { labels: ["Understanding"], nodes: ["profile"] } });
    expect(second[0]).toEqual({ type: "data-step", id: "steps", data: { labels: ["Understanding", "Locating"], nodes: ["profile", "locate"] } });

    const answer = [
      ...toUIChunks(
        { type: "answer", answer: "**Sushi World**", cards: [{ type: "restaurant", id: "osm:node/1", name: "Sushi World" }],
          route: ["web"], trace_id: "abc", location: "Laureles", seconds: 12, language: "Spanish",
          understood: ["sushi"] },
        state,
      ),
    ];
    expect(answer.map((c) => c.type)).toEqual(["text-start", "text-delta", "text-end", "data-result"]);
    expect(answer[1]).toMatchObject({ delta: "**Sushi World**" });
    expect(answer[3]).toMatchObject({
      data: { traceId: "abc", location: "Laureles", route: ["web"], language: "Spanish", understood: ["sushi"] },
    });
  });

  it("passes the remembered profile through, and null when the backend sends none", () => {
    const memory = { location: "Laureles", dietary: ["vegan"], allergies: ["peanut"], from_saved: true };
    const state = { steps: [] as string[], nodes: [] as string[], textId: "t" };
    const withMemory = [...toUIChunks({ type: "answer", answer: "ok", cards: [], route: [], memory }, state)];
    expect(withMemory[3]).toMatchObject({ data: { memory } });
    const without = [...toUIChunks({ type: "answer", answer: "ok", cards: [], route: [] }, state)];
    expect(without[3]).toMatchObject({ data: { memory: null } });
  });

  it("turns backend errors into a safe error chunk", () => {
    const chunks = [...toUIChunks({ type: "error", message: "Something went wrong" }, { steps: [], nodes: [], textId: "t" })];
    expect(chunks).toEqual([{ type: "error", errorText: "Something went wrong" }]);
  });
});

describe("toBackendRequest", () => {
  const msg = (id: string, role: "user" | "assistant", text: string, extra: object[] = []): ChatMessage =>
    ({ id, role, parts: [...extra, { type: "text", text }] }) as ChatMessage;

  it("sends the last user message and previous turns as history, text only", () => {
    const messages = [
      msg("1", "user", "sushi near me"),
      msg("2", "assistant", "Where are you?", [{ type: "data-step", data: { labels: ["x"] } }]),
      msg("3", "user", "Laureles, Medellín"),
    ];
    expect(toBackendRequest(messages)).toEqual({
      message: "Laureles, Medellín",
      history: [
        { role: "user", content: "sushi near me" },
        { role: "assistant", content: "Where are you?" },
      ],
    });
  });

  it("caps the message length and the number of history turns", () => {
    const many = Array.from({ length: 30 }, (_, i) => msg(String(i), i % 2 ? "assistant" : "user", `turn ${i}`));
    const req = toBackendRequest([...many, msg("x", "user", "y".repeat(5000))]);
    expect(req.message).toHaveLength(2000);
    expect(req.history).toHaveLength(10);
  });
});

describe("coordinates", () => {
  it("rounds to ~1 km and rejects invalid input", () => {
    expect(roundCoordinates(6.24423, -75.58121)).toEqual({ lat: 6.24, lon: -75.58 });
    expect(parseCoordinates({ lat: 6.2442, lon: -75.5812 })).toEqual({ lat: 6.24, lon: -75.58 });
    expect(parseCoordinates({ lat: 120, lon: 0 })).toBeNull();
    expect(parseCoordinates({ lat: "6", lon: 1 })).toBeNull();
    expect(parseCoordinates(null)).toBeNull();
  });
});

describe("answerWithoutPickLists", () => {
  it("drops the numbered pick lists and keeps the summary and notes", () => {
    const text = [
      "Three low-lit Italian rooms in Pasadena.",
      "",
      "**Restaurants**",
      "1. **Osteria** (Italian) - _California guide_  ",
      "   Candle-lit booths.",
      "2. **Vino** (Wine bar)",
      "",
      "_Place data © OpenStreetMap contributors_",
    ].join("\n");
    expect(answerWithoutPickLists(text)).toBe("Three low-lit Italian rooms in Pasadena.\n\n_Place data © OpenStreetMap contributors_");
  });

  it("leaves answers without lists untouched", () => {
    expect(answerWithoutPickLists("¿Dónde estás?")).toBe("¿Dónde estás?");
  });
});

describe("small UI helpers", () => {
  it("shortens place names and detects Spanish", () => {
    expect(shortPlace("Laureles, Comuna 11, Medellín, Colombia")).toBe("Laureles, Comuna 11");
    expect(isSpanish("Spanish")).toBe(true);
    expect(isSpanish("English")).toBe(false);
    expect(isSpanish(null)).toBe(false);
  });

  it("titles a chat from its first user message and keeps the newest first", () => {
    const m = (text: string): ChatMessage => ({ id: text, role: "user", parts: [{ type: "text", text }] });
    expect(chatTitle([])).toBe("New chat");
    expect(chatTitle([m("x".repeat(60))])).toHaveLength(41);
    const a = { id: "a", title: "A", messages: [], updatedAt: 1 };
    const b = { id: "b", title: "B", messages: [], updatedAt: 2 };
    expect(upsertChat([a, b], { ...b, title: "B2" }).map((c) => c.title)).toEqual(["B2", "A"]);
  });
});

describe("memory", () => {
  it("lists allergies first, then diet, and the city only when it was saved earlier", () => {
    const memory = { location: "Laureles, Medellín", dietary: ["vegan"], allergies: ["peanut", "kiwi"], from_saved: true };
    expect(memoryChips(memory, true)).toEqual([
      { label: "Alergia al maní", kind: "allergy" },
      { label: "kiwi", kind: "allergy" },
      { label: "vegan", kind: "diet" },
      { label: "Ciudad guardada: Laureles, Medellín", kind: "place" },
    ]);
    expect(memoryChips({ ...memory, from_saved: false }, false).map((c) => c.label)).toEqual([
      "Peanut allergy", "kiwi", "vegan",
    ]);
    expect(memoryChips(null, false)).toEqual([]);
  });

  it("builds the same session id the chat route sends", () => {
    expect(toSessionId("abc_DEF-123")).toBe("abc_DEF-123");
    expect(toSessionId("github:42/x")).toBe("github-42-x");
    expect(toSessionId("x".repeat(80))).toHaveLength(64);
  });
});
