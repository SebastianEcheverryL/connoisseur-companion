import { describe, expect, it } from "vitest";

import { toUIChunks } from "./adapter";
import { parseCoordinates, roundCoordinates } from "./geo";
import { toBackendRequest } from "./history";
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
    const state = { steps: [] as string[], textId: "t1" };
    const first = [...toUIChunks({ type: "step", node: "profile", label: "Understanding" }, state)];
    const second = [...toUIChunks({ type: "step", node: "locate", label: "Locating" }, state)];
    expect(first[0]).toEqual({ type: "data-step", id: "steps", data: { labels: ["Understanding"] } });
    expect(second[0]).toEqual({ type: "data-step", id: "steps", data: { labels: ["Understanding", "Locating"] } });

    const answer = [
      ...toUIChunks(
        { type: "answer", answer: "**Sushi World**", cards: [{ type: "restaurant", id: "osm:node/1", name: "Sushi World" }],
          route: ["web"], trace_id: "abc", location: "Laureles", seconds: 12 },
        state,
      ),
    ];
    expect(answer.map((c) => c.type)).toEqual(["text-start", "text-delta", "text-end", "data-result"]);
    expect(answer[1]).toMatchObject({ delta: "**Sushi World**" });
    expect(answer[3]).toMatchObject({ data: { traceId: "abc", location: "Laureles", route: ["web"] } });
  });

  it("turns backend errors into a safe error chunk", () => {
    const chunks = [...toUIChunks({ type: "error", message: "Something went wrong" }, { steps: [], textId: "t" })];
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
