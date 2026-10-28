# 0002. AWS Lambda (container image) instead of ECS Fargate

- Status: accepted (2026-10-20)
- Related: [0001](0001-bedrock-instead-of-gpu.md), [0003](0003-onnx-embeddings-instead-of-torch.md), [0004](0004-vercel-frontend-lambda-backend.md)

## Context

The backend is a FastAPI app that streams server-sent events while a LangGraph workflow runs for 5 to 40 seconds. It needs the Chroma vector index, two embedding models and the Prompt Guard classifier in memory, and outbound access to Bedrock, OpenStreetMap, Tavily and Langfuse. Traffic is bursty and usually zero.

## Decision

Run the API on **AWS Lambda as a container image** (`x86_64`, 2,048 MB, 300 s timeout) behind a **Function URL with IAM auth and response streaming**. The [AWS Lambda Web Adapter](https://github.com/awslabs/aws-lambda-web-adapter) runs the unchanged FastAPI app, so the same image runs locally with `docker run`.

The image (~1.5 GB) bakes in the index and every model, so a cold start never downloads anything. Lambda's filesystem is read-only except `/tmp`, so the index is copied there at start and models load offline (`HF_HUB_OFFLINE=1`). CI runs the image with `docker run --read-only` to catch anything that writes elsewhere.

## Alternatives considered

- **ECS Fargate behind an Application Load Balancer.** No cold starts and no time limit, but the smallest always-on task plus the load balancer cost roughly $25 a month before any traffic, several times the expected Bedrock spend.
- **App Runner.** Simpler than ECS, but it bills a provisioned instance while idle and adds little over Lambda for this workload.
- **API Gateway in front of Lambda.** API Gateway's integration timeout is 29 seconds by default, which a full answer can exceed, and REST APIs do not pass SSE through. The Function URL streams for up to 15 minutes.
- **Lambda zip package.** The models and index exceed the 250 MB unzipped limit; container images allow 10 GB.

## Consequences

- **$0 so far.** The October bill shows Lambda at $0.00: usage is far inside the free tier (400,000 GB-seconds a month).
- **Cold starts of 10 to 30 seconds** while the index is copied and models load. The answer streams progress steps, so the user sees the agent working; the runbook covers the Vercel timeout this can cause on the first message.
- **Concurrency is a quota.** New accounts get 10 concurrent executions, so reserved concurrency defaults to unreserved (`-1`). Setting it to `0` is the kill switch in the [runbook](../runbook.md#emergency-stop).
- **State lives outside the function.** Quotas and conversation memory are DynamoDB items with TTL, so every instance sees the same counters. Secrets are read from SSM Parameter Store at cold start, which is why a changed secret needs a new instance to take effect.
- **Who deploys what.** Terraform owns the infrastructure and is applied by hand; GitHub Actions only pushes images and calls `update-function-code`. Terraform ignores `image_uri`, so an apply never rolls back a deploy.
