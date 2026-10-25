import { auth, githubConfigured } from "@/auth";

export type AppUser = { id: string; name: string; image?: string | null; guest: boolean };

/**
 * The current user, or null when sign-in is required.
 * Without GitHub credentials, local development runs as a guest; production never does.
 */
export async function currentUser(): Promise<AppUser | null> {
  if (githubConfigured) {
    const session = await auth();
    if (!session?.user?.id) return null;
    return { id: `github:${session.user.id}`, name: session.user.name ?? "GitHub user", image: session.user.image,
             guest: false };
  }
  if (process.env.NODE_ENV !== "production") return { id: "dev-guest", name: "Local developer", guest: true };
  return null;
}
