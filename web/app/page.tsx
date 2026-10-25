import { Suspense } from "react";

import { githubConfigured, signIn, signOut } from "@/auth";
import ChatApp from "@/components/ChatApp";
import { Logo } from "@/components/Sidebar";
import { currentUser } from "@/lib/session";

export default function Home() {
  return (
    <Suspense fallback={<Shell />}>
      <Gate />
    </Suspense>
  );
}

/** Reads the session at request time (behind Suspense, as Cache Components requires). */
async function Gate() {
  const user = await currentUser();
  if (!user) return <SignIn />;
  const account = user.guest ? null : (
    <form
      action={async () => {
        "use server";
        await signOut({ redirectTo: "/" });
      }}
    >
      <button className="rounded-full border border-border px-2.5 py-1 text-xs font-semibold text-foreground hover:bg-surface-2">
        Sign out
      </button>
    </form>
  );
  return <ChatApp user={{ name: user.name, image: user.image, guest: user.guest }} account={account} />;
}

function SignIn() {
  return (
    <main className="mx-auto flex w-full max-w-md flex-1 flex-col items-center justify-center gap-5 px-4 text-center">
      <Logo size={48} />
      <h1 className="font-display text-[34px] leading-tight">Connoisseur Companion</h1>
      <p className="text-pretty text-muted">
        Restaurant and recipe recommendations from a multi-agent assistant. Sign in to start; each account has a
        daily message limit.
      </p>
      {githubConfigured ? (
        <form
          action={async () => {
            "use server";
            await signIn("github", { redirectTo: "/" });
          }}
        >
          <button className="h-11 rounded-full bg-accent px-6 text-sm font-semibold text-on-accent hover:opacity-90">
            Sign in with GitHub
          </button>
        </form>
      ) : (
        <p className="text-sm text-muted">Sign-in is not configured on this deployment.</p>
      )}
    </main>
  );
}

function Shell() {
  return (
    <div className="flex h-dvh w-full" aria-busy="true">
      <div className="hidden w-64 flex-none border-r border-border bg-surface md:block" />
      <div className="flex-1 px-4 py-10 text-sm text-muted md:px-[88px]">Loading…</div>
    </div>
  );
}
