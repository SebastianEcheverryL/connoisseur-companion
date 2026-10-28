# 0005. Deterministic LangGraph routing with an optional plan-then-execute orchestrator

- Status: accepted (2026-10-27); enabled in production
- Related: [0001](0001-bedrock-instead-of-gpu.md), [evaluation report](../evals/report.md#model-choice-and-the-hybrid-orchestrator-2026-10-27-bedrock)

## Context

The agent workflow is a LangGraph graph: a profile agent extracts intent and preferences, then a fixed branch answers (local retrieval, web search, a place lookup, recipe steps, a fixed reply). Each branch is deterministic and grounded: it can only show places and dishes that its sources contain.

Real conversations asked for things no single branch serves: "a moody Italian spot in Pasadena and what to order at the first one". Letting an LLM pick tools freely (a ReAct loop) would handle that, but web content would then sit in the same context that chooses the next tool, which is the classic path for indirect prompt injection.

## Decision

Keep the fixed graph and add an **optional hybrid orchestrator** (`ORCHESTRATOR_ENABLED`, `src/connoisseur/agents/orchestrator.py`):

- The orchestrator chooses among **high-level tools that are the existing branches** (`search_restaurants`, `place_info`, `search_recipes`, `recipe_steps`, `ask_user`, `answer_canned`, in `agents/skills.py`). Each tool stays deterministic inside and writes its own section of the answer.
- It sees only **counts and names, tagged as data**, never review text or web pages.
- It **plans its calls once, then executes them**. It gets another round only to fix an error, or when the message asks for several things and only one call was planned.
- **Positions are resolved in code** ("el segundo" is card 2, whatever name the model passes).
- On any failure, or if it chooses no tool, the fixed routing answers. Greetings and refusals skip it.

## Alternatives considered

| Option | Multi-turn suite (14 conversations, Nova 2 Lite) | Median per conversation |
|---|---|---|
| Fixed routing only | 13 / 14 (cannot serve the compound request) | 7.2 s |
| Orchestrator asking the model again after every tool | 14 / 14 | 17.0 s |
| **Orchestrator, plan then execute** | **14 / 14** | **9.2 s** |

A **free ReAct agent over low-level tools** was not tried for the whole workflow: the read-only lookup agent already uses ReAct, and it was the component that invented facts (27% of answers before the grounding check).

## Consequences

- **Compound requests work** for about 2 seconds more per conversation and one extra LLM call per message.
- **The security model holds.** Tool choice never depends on web content, and the red team passes 16/16 with the orchestrator on.
- **Two code paths to test.** The evaluation reports record which mode ran, and the fallback means a broken orchestrator degrades to the old behaviour instead of failing.
- **Turning it off is one Terraform variable** (`orchestrator_enabled`).
