import type { BackendEvent } from "./types";

/**
 * Parse a server-sent events byte stream into JSON events.
 * Handles events split across chunks and ignores comments / keep-alives.
 */
export async function* parseSSE(stream: ReadableStream<Uint8Array>): AsyncGenerator<BackendEvent> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const event = parseBlock(buffer.slice(0, boundary));
      buffer = buffer.slice(boundary + 2);
      if (event) yield event;
      boundary = buffer.indexOf("\n\n");
    }
  }
  const last = parseBlock(buffer);
  if (last) yield last;
}

function parseBlock(block: string): BackendEvent | null {
  const data = block
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart())
    .join("\n");
  if (!data) return null;
  try {
    const parsed = JSON.parse(data);
    if (parsed && typeof parsed === "object" && "type" in parsed) return parsed as BackendEvent;
    // The backend's final "done" event has an empty JSON body
    return block.includes("event: done") ? { type: "done" } : null;
  } catch {
    return null;
  }
}
