# 0004. Next.js on Vercel calling Lambda through a backend-for-frontend with OIDC

- Status: accepted (2026-10-20)
- Related: [0002](0002-lambda-instead-of-ecs.md), [web/README.md](../../web/README.md)

## Context

The chat needs a public web UI with sign-in (quotas and memory are per user), streamed agent steps, a map and feedback buttons. The backend must not be callable by anyone on the internet: every message spends Bedrock money. Storing long-lived AWS keys in a hosting provider was ruled out.

## Decision

- **Frontend:** Next.js 16 on **Vercel Hobby**, with GitHub sign-in (Auth.js v5) and the Vercel AI SDK `useChat`.
- **Backend-for-frontend:** the browser only talks to Next.js route handlers (`/api/chat`, `/api/feedback`, `/api/memory`). They check the session, attach the signed-in user id, and translate the API's server-sent events into the AI SDK stream protocol.
- **Service-to-service auth:** the Lambda Function URL requires **SigV4 (`AWS_IAM`)**. The BFF gets short-lived AWS credentials through **Vercel OIDC federation** and signs each request. The IAM role trusts one Vercel project's production environment only. Because only that role can reach the function, the backend trusts the `X-User-Id` header it sends (`TRUST_USER_HEADER=true`).

## Alternatives considered

- **Static site on S3 + CloudFront, browser calling the API directly.** The browser would need a credential to call the API, and quotas would be keyed by something the user controls.
- **AWS Amplify Hosting.** Would keep everything in one cloud, but Vercel runs Next.js 16 features (streaming route handlers, partial prerendering) first and is free at this scale.
- **A shared bearer API key stored in Vercel.** Supported by the backend (`API_KEY`) for other deployments, but it is a long-lived secret; OIDC removes it.

## Consequences

- **$0 for hosting** on the Vercel Hobby plan, and no AWS secret outside AWS.
- **Two platforms to operate.** Deploys are separate (Vercel on push, GitHub Actions for the image), and a cold Lambda plus a long answer can hit the Vercel function's time limit on the first message (`maxDuration = 300`). The [runbook](../runbook.md) covers both.
- **The BFF is a security boundary.** It forwards only the fields the API accepts, and the site sends a strict Content Security Policy. Raw tool output (`evidence`) is removed by the API itself, so it never reaches Vercel either.
- **The MCP server is separate.** MCP clients cannot use Vercel sessions, so the optional public MCP function exposes only `/mcp` and `/health` with a bearer token (`EXPOSE_CHAT=false`).
