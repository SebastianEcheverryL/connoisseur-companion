import { Suspense } from "react";

import { githubConfigured, signIn, signOut } from "@/auth";
import Chat from "@/components/Chat";
import { currentUser } from "@/lib/session";

export default function Home() {
  return (
    <div className="flex flex-1 flex-col">
      <Suspense fallback={<Shell />}>
        <Gate />
      </Suspense>
    </div>
  );
}

/** Reads the session at request time (behind Suspense, as Cache Components requires). */
async function Gate() {
  const user = await currentUser();
  if (!user) return <SignIn />;
  return (
    <>
      <Header name={user.name} image={user.image} guest={user.guest} />
      <Chat />
    </>
  );
}

function Header({ name, image, guest }: { name: string; image?: string | null; guest: boolean }) {
  return (
    <header className="border-b border-border bg-surface/80 backdrop-blur">
      <div className="mx-auto flex max-w-3xl items-center justify-between gap-3 px-4 py-3">
        <div className="min-w-0">
          <h1 className="truncate text-base font-semibold">Connoisseur Companion</h1>
          <p className="truncate text-xs text-muted">Restaurants anywhere · recipes · sources you can check</p>
        </div>
        <div className="flex shrink-0 items-center gap-2 text-sm">
          {image ? (
            // eslint-disable-next-line @next/next/no-img-element -- GitHub avatar, external URL
            <img src={image} alt="" className="h-7 w-7 rounded-full" />
          ) : null}
          <span className="hidden text-muted sm:inline">{guest ? `${name} (dev)` : name}</span>
          {!guest && (
            <form
              action={async () => {
                "use server";
                await signOut({ redirectTo: "/" });
              }}
            >
              <button className="rounded-md border border-border px-2 py-1 text-xs hover:bg-accent-soft">
                Sign out
              </button>
            </form>
          )}
        </div>
      </div>
    </header>
  );
}

function SignIn() {
  return (
    <main className="mx-auto flex w-full max-w-md flex-1 flex-col items-center justify-center gap-5 px-4 text-center">
      <h1 className="text-2xl font-semibold">Connoisseur Companion</h1>
      <p className="text-muted">
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
          <button className="rounded-lg bg-foreground px-4 py-2 text-sm font-medium text-background hover:opacity-90">
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
    <div className="mx-auto w-full max-w-3xl flex-1 px-4 py-10 text-sm text-muted" aria-busy="true">
      Loading…
    </div>
  );
}
