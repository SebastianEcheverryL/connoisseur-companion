import NextAuth from "next-auth";
import GitHub from "next-auth/providers/github";

/** GitHub login is enabled when AUTH_GITHUB_ID / AUTH_GITHUB_SECRET are set (Auth.js reads them itself). */
export const githubConfigured = Boolean(process.env.AUTH_GITHUB_ID && process.env.AUTH_GITHUB_SECRET);

export const { handlers, auth, signIn, signOut } = NextAuth({
  providers: githubConfigured ? [GitHub] : [],
  session: { strategy: "jwt" },
  callbacks: {
    session({ session, token }) {
      // Stable per-user id, forwarded to the backend for per-user quotas
      if (session.user && token.sub) session.user.id = token.sub;
      return session;
    },
  },
});
