import { backendFetch } from "@/lib/backend";
import { toSessionId } from "@/lib/memory";
import { currentUser } from "@/lib/session";

/** "Forget my preferences": deletes what the backend remembers about the user and about this chat. */
export async function DELETE(req: Request) {
  const user = await currentUser();
  if (!user) return Response.json({ error: "Sign in required" }, { status: 401 });

  const chat = new URL(req.url).searchParams.get("chat") ?? "";
  if (chat && !/^[\w-]{1,64}$/.test(chat)) return Response.json({ error: "Invalid chat id" }, { status: 400 });

  const query = chat ? `?session_id=${encodeURIComponent(toSessionId(chat))}` : "";
  const res = await backendFetch(`/memory${query}`, { method: "DELETE", userId: user.id }).catch(() => null);
  return Response.json({ forgotten: res?.ok ?? false }, { status: res?.ok ? 200 : 502 });
}
