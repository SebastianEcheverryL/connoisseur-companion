# Architecture decision records

Short records of the decisions that shaped the production version: what was decided, what else was considered, and what it cost. Each one is written for someone reading the code for the first time.

| # | Decision | Status |
|---|---|---|
| [0001](0001-bedrock-instead-of-gpu.md) | Serverless models on Amazon Bedrock instead of a self-hosted GPU | Accepted |
| [0002](0002-lambda-instead-of-ecs.md) | AWS Lambda (container image) instead of ECS Fargate | Accepted |
| [0003](0003-onnx-embeddings-instead-of-torch.md) | ONNX embeddings with `fastembed` instead of PyTorch | Accepted |
| [0004](0004-vercel-frontend-lambda-backend.md) | Next.js on Vercel calling Lambda through a backend-for-frontend with OIDC | Accepted |
| [0005](0005-fixed-graph-with-optional-orchestrator.md) | Deterministic LangGraph routing with an optional plan-then-execute orchestrator | Accepted |

Format: context, decision, alternatives considered, consequences. Numbers come from the [evaluation report](../evals/report.md), the [red-team report](../security/redteam-report.md) and the AWS bill.
