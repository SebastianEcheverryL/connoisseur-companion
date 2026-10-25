/**
 * Server-side client for the FastAPI backend. Never imported by client components.
 *
 * Two modes (BACKEND_AUTH):
 *  - "bearer"  (local / any host): Authorization: Bearer BACKEND_API_KEY
 *  - "aws_iam" (AWS Lambda Function URL with IAM auth): requests are signed with SigV4 using
 *    short-lived credentials obtained through Vercel OIDC federation - no long-lived AWS keys.
 * In both modes the signed-in user's id is forwarded so the backend can enforce per-user quotas.
 */
import { AwsClient } from "aws4fetch";

type Mode = "bearer" | "aws_iam";

function config() {
  const url = process.env.BACKEND_URL ?? "http://localhost:8000";
  const mode = (process.env.BACKEND_AUTH ?? "bearer") as Mode;
  return { url: url.replace(/\/$/, ""), mode };
}

let awsClient: { client: AwsClient; expires: number } | null = null;

async function signedFetcher(): Promise<AwsClient> {
  if (awsClient && awsClient.expires > Date.now() + 60_000) return awsClient.client;
  const roleArn = process.env.AWS_ROLE_ARN;
  const region = process.env.AWS_REGION ?? "us-east-1";
  if (!roleArn) throw new Error("BACKEND_AUTH=aws_iam requires AWS_ROLE_ARN");
  const { awsCredentialsProvider } = await import("@vercel/functions/oidc");
  const creds = await awsCredentialsProvider({ roleArn, clientConfig: { region } })();
  const client = new AwsClient({
    accessKeyId: creds.accessKeyId,
    secretAccessKey: creds.secretAccessKey,
    sessionToken: creds.sessionToken,
    service: "lambda",
    region,
  });
  awsClient = { client, expires: creds.expiration?.getTime() ?? Date.now() + 15 * 60_000 };
  return client;
}

export async function backendFetch(
  path: string,
  init: { method?: string; body?: unknown; userId: string; signal?: AbortSignal },
): Promise<Response> {
  const { url, mode } = config();
  const headers: Record<string, string> = {
    "content-type": "application/json",
    "x-user-id": init.userId,
  };
  const request = {
    method: init.method ?? "POST",
    headers,
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
    signal: init.signal,
  };
  if (mode === "aws_iam") {
    return (await signedFetcher()).fetch(`${url}${path}`, request);
  }
  if (process.env.BACKEND_API_KEY) headers.authorization = `Bearer ${process.env.BACKEND_API_KEY}`;
  return fetch(`${url}${path}`, request);
}
