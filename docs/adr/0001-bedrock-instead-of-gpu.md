# 0001. Serverless models on Amazon Bedrock instead of a self-hosted GPU

- Status: accepted (2026-10-20; model updated 2026-10-27)
- Related: [0002](0002-lambda-instead-of-ecs.md), [evaluation report](../evals/report.md#model-choice-and-the-hybrid-orchestrator-2026-10-27-bedrock)

## Context

The project started fully local: `llama3.1` 8B on Ollama for the agents and the judge, Llama Guard 3 1B for guardrails. The deployed demo has to answer strangers at any hour, cost a few dollars a month at most (the budget is AWS credits) and use no paid third-party LLM API.

A conversation makes about 6 LLM calls of ~1,400 input and ~110 output tokens each. Demo traffic is a few dozen conversations a day at most, and zero most of the time.

## Decision

Run the agents on **Amazon Bedrock on-demand** and keep Ollama for local development, behind one provider factory (`src/connoisseur/llm.py`, `LLM_PROVIDER=ollama|bedrock`). Guardrails follow the same switch: Bedrock Guardrails in production, Llama Guard + Prompt Guard locally.

The model is **Amazon Nova 2 Lite** (`us.amazon.nova-2-lite-v1:0`), chosen by a bake-off of five Bedrock models on the profile suite (30 cases) and the multi-turn suite (14 conversations):

| Model | Profile cases fully correct | Median per call | Price in / out per M tokens |
|---|---|---|---|
| Nova Lite (first deployed) | 90% | 1.4 s | $0.06 / $0.24 |
| **Nova 2 Lite** | **100%** | 1.6 s | $0.33 / $2.75 |
| gpt-oss-120b | 100% | 2.5 s (whole chats ~2x slower) | $0.15 / $0.60 |
| Qwen3 32B | 97% | 1.4 s | $0.15 / $0.60 |
| Ministral 3 14B | never returned parseable structured output | 1.3 s | $0.20 / $0.20 |

## Alternatives considered

- **GPU instance running Ollama or vLLM** (e.g. `g5.xlarge`, about $1 an hour on demand). Always-on, it costs around $700 a month, more than the whole credit balance, for a demo that is idle almost all the time. Scale-to-zero on GPU adds minutes of cold start.
- **SageMaker real-time endpoint.** Same always-on cost profile, plus endpoint management. Serverless inference has no GPU.
- **Paid LLM APIs outside AWS.** Ruled out by the project constraint; they would also add a second vendor, key and billing.
- **Keep `llama3.1` 8B.** On a laptop a full recommendation took ~30 s and structured extraction was ~10x slower than Nova; hosting it needs the GPU above.

## Consequences

- **Cost follows usage.** Nova 2 Lite costs about half a cent per conversation. The October bill for Bedrock was **$1.37**, which includes the model bake-off, the evaluation suites and the red-team runs, not only real traffic.
- **Two runtimes to keep honest.** Prompts and schemas must work on both `llama3.1` and Nova. Lessons that came from this: schemas with every field required (a small model answers `{}` to an all-optional schema), fixed text for help and dish lists (Nova Lite wrote placeholders such as `<link_to_menu>`), and grounding revisions forced into the answer language (Nova translated Spanish revisions to English).
- **The swap is one setting.** Changing the model is `bedrock_model_id` in `terraform.tfvars`; IAM grants access to that one model only.
- **Bedrock Guardrails is not enough alone.** The red team showed its prompt-attack filter missing a Spanish override, so the local injection screen runs in front of it (see [SECURITY.md](../../SECURITY.md#guardrail-providers)).
