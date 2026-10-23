import { createUIMessageStream, createUIMessageStreamResponse, generateId } from "ai";

import { toUIChunks } from "@/lib/adapter";
import { backendFetch } from "@/lib/backend";
import { parseCoordinates } from "@/lib/geo";
import { toBackendRequest } from "@/lib/history";
import { currentUser } from "@/lib/session";
import { parseSSE } from "@/lib/sse";
import type { ChatMessage } from "@/lib/types";

// The agent can take a while on local models; Vercel functions default to a shorter limit
export const maxDuration = 300;

const FRIENDLY_ERRORS: Record<number, string> = {
  401: "The assistant rejected the request (authentication).",
  422: "That message could not be processed. Try a shorter one.",
  429: "You've reached the message limit for now. Please try again later.",
};

export async function POST(req: Request) {
  const user = await currentUser();
  if (!user) return Response.json({ error: "Sign in required" }, { status: 401 });

  const body = (await req.json().catch(() => null)) as
    | { id?: unknown; messages?: ChatMessage[]; location?: unknown }
    | null;
  const { message, history } = toBackendRequest(body?.messages ?? []);
  if (!message) return Response.json({ error: "Empty message" }, { status: 400 });

  const location = parseCoordinates(body?.location);
  // One Langfuse session per conversation (the chat id from useChat), falling back to the user
  const sessionId = (typeof body?.id === "string" && body.id ? body.id : user.id).replace(/[^\w-]/g, "-").slice(0, 64);
  const backend = await backendFetch("/chat", {
    userId: user.id,
    signal: req.signal,
    body: { message, history, location, session_id: sessionId },
  }).catch((error: unknown) => {
    console.error("backend unreachable", error);
    return null;
  });

  const stream = createUIMessageStream<ChatMessage>({
    async execute({ writer }) {
      writer.write({ type: "start" });
      if (!backend || !backend.ok || !backend.body) {
        const status = backend?.status ?? 503;
        writer.write({
          type: "error",
          errorText: FRIENDLY_ERRORS[status] ?? "The assistant is not available right now. Please try again.",
        });
        return;
      }
      const state = { steps: [] as string[], nodes: [] as string[], textId: generateId() };
      for await (const event of parseSSE(backend.body)) {
        for (const chunk of toUIChunks(event, state)) writer.write(chunk);
      }
      writer.write({ type: "finish" });
    },
    onError: () => "Something went wrong while answering. Please try again.",
  });
  return createUIMessageStreamResponse({ stream });
}
