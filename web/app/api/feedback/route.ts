import { z } from "zod";

import { backendFetch } from "@/lib/backend";
import { currentUser } from "@/lib/session";

const Feedback = z.object({
  traceId: z.string().regex(/^[0-9a-f]{32}$/),
  value: z.union([z.literal(0), z.literal(1)]),
  comment: z.string().max(1000).optional(),
});

/** Thumbs up/down on an answer, forwarded to the backend as a Langfuse score on that answer's trace. */
export async function POST(req: Request) {
  const user = await currentUser();
  if (!user) return Response.json({ error: "Sign in required" }, { status: 401 });

  const parsed = Feedback.safeParse(await req.json().catch(() => null));
  if (!parsed.success) return Response.json({ error: "Invalid feedback" }, { status: 400 });

  const { traceId, value, comment } = parsed.data;
  const res = await backendFetch("/feedback", { userId: user.id, body: { trace_id: traceId, value, comment } })
    .catch(() => null);
  // 503 = tracing not configured on the backend: not an error worth showing to the user
  return Response.json({ recorded: res?.ok ?? false }, { status: res && res.status !== 503 && !res.ok ? 502 : 200 });
}
