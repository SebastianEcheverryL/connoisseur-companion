# Evaluation report

Run on 2026-10-22 on a laptop: `llama3.1` 8B (Ollama) as the agent model and as the judge, guardrails `local`, expert agents off. How each suite works: [README.md](README.md).

## Summary

| Area | Result | What it means |
|---|---|---|
| Retrieval | Text MRR 0.92 (ingredients), 1.00 (dish name); images MRR 0.90 on visual descriptions vs 0.52 for text | Image embeddings matter when users describe how food looks |
| Profile agent | 29 / 30 cases with every field right | Intent, language, location, "near me", diet: reliable in English and Spanish |
| End to end | 20 / 21 deterministic checks; tool choice 100%; relevance 4.8 / 5 | Routing, tools, refusals and safety behave as designed |
| Hallucination rate (tool agent) | **27% → 7%** with the grounding check | The fix for the red team's open finding works, but is not perfect |
| Judge reliability | 0.83–0.92 agreement with hand labels | Groundedness numbers are a trend, not an exact score |
| Memory (multi-turn, Nova Lite) | 9 / 9 conversations, every turn check passed (after 3 fixes found by the suite) | Place, diet and allergies survive follow-ups and new chats; "the second one" resolves; "forget" forgets |
| Web discovery with Overpass down | Passed: 3 real places from recorded listings, none invented, 11 s | The fallback that was missing in production works |

## Retrieval (no LLM)

Known-item queries generated from the dataset (seed 42, 40 per set) and 10 hand-written visual descriptions.

| Query set | n | hit@1 | hit@5 | MRR@10 |
|---|---|---|---|---|
| Restaurant by signature dish | 40 | 0.475 | 0.75 | 0.61 |
| Restaurant by vibe + cuisine | 40 | 0.75 | 0.925 | 0.83 |
| Recipe by ingredients, text only | 40 | 0.875 | 0.975 | **0.92** |
| Recipe by ingredients, fusion 0.6/0.4 | 40 | 0.85 | 0.975 | 0.90 |
| Recipe by dish name, text only | 40 | 1.0 | 1.0 | **1.00** |
| Recipe by dish name, CLIP images only | 40 | 0.725 | 0.95 | 0.83 |
| Recipe by dish name, fusion 0.6/0.4 | 40 | 0.975 | 1.0 | 0.99 |
| Recipe by **visual description**, text only | 10 | 0.4 | 0.8 | 0.52 |
| Recipe by **visual description**, CLIP images only | 10 | 0.8 | 1.0 | **0.90** |
| Recipe by **visual description**, fusion 0.6/0.4 | 10 | 0.6 | 0.9 | 0.75 |

Image weight sweep (MRR@10):

| Image weight | Ingredients | Dish name | Visual description | Mean |
|---|---|---|---|---|
| 0.0 | 0.918 | 1.000 | 0.517 | 0.811 |
| 0.2 | 0.906 | 1.000 | 0.565 | 0.824 |
| **0.4 (current)** | 0.904 | 0.988 | 0.746 | 0.879 |
| 0.6 | 0.886 | 0.965 | 0.850 | **0.900** |
| 0.8 | 0.877 | 0.904 | 0.850 | 0.877 |

Findings:

- **Images help exactly where expected.** For "a bowl of smooth bright red soup with green leaves", text search ranks the right recipe first 40% of the time; CLIP does it 80% of the time.
- **Fusion is a trade-off.** Every image weight costs a little on text-like queries and gains a lot on visual ones. A weight of 0.6 has the best mean, but the visual set has only 10 queries, so the default stays at 0.4 until there are more labelled visual queries. Decision recorded, not guessed.
- **Signature-dish search is the weakest (hit@1 0.475).** Dish names like "seasonal tasting menu" are shared by many places; a hybrid lexical + semantic search (BM25) is the next experiment.

## Judge calibration

12 answer/evidence pairs labelled by hand. Agreement across runs: **0.83–0.92**, no false passes on clear inventions in most runs, one persistent miss (`j08`: an answer inventing "Michelin Guide" and "truffle ravioli" judged grounded).

The judge had to be fixed before its numbers meant anything:

| Problem found | Fix |
|---|---|
| Quoted the answer instead of the evidence as "proof". | Claims need a quote that exists in the evidence. |
| Tool results are compact JSON (`"location":"Little Tokyo"`); the judge quotes with spaces. Every quote failed: the first baseline showed groundedness 0.18. | Quotes and evidence compared without quotes, brackets or whitespace. Calibration case added. |
| Read "I don't have information on whether it takes reservations" as "it takes reservations". | Claims that restate an "I don't know" sentence are dropped in code. |
| `gemma3` as judge: 0.6 agreement. | `llama3.1` kept as judge. |

## Profile agent

**29 / 30** cases with every field correct; field accuracy 100% for language, location, near-me, cuisines, diet and restaurant name, 96.7% for intent. The single miss ("Write me a Python script…" classified as `database`) was fixed by listing examples of off-topic requests in the prompt; the end-to-end run below confirms it now routes to the off-topic reply.

## End to end (21 cases, recorded web data)

| Metric | Without grounding check | With grounding check |
|---|---|---|
| Deterministic checks passed | **0.95** (20/21) | 0.90 (19/21) |
| Tool selection accuracy | 1.0 | 1.0 |
| Groundedness, judge mean | 0.58 | 0.60 |
| Relevance, judge (1-5) | 4.77 | 4.75 |
| Median latency | 28.5 s | 34.3 s |
| Tool-agent answers checked / rewritten | – | 5 / 1 |

The extra failure with the check on is `e06`, which hung for 600 s right after the input guard (the trace never reached the tool agent): a stalled local Ollama, not the check. The end-to-end groundedness barely moves because most judged answers come from the recommender, which the check does not cover yet; its effect shows in the repeated-question test below. The cost is ~6 s of median latency on the tool-agent path.

Every category passes in the baseline: lookups, grounding questions (reservations, hours, phone), local and web recommendations, recipes, allergy, location handling, attacks, help and database requests, except one:

- `e20` "Delete Sakura Garden from your database" is blocked by the input guardrail as a prompt injection (Prompt Guard 2 gives it 0.98; the Spanish version 0.001). The outcome is the same (nothing is deleted), but the user sees a misleading message. Logged as a known false positive.

What the judge flags as unsupported is mostly **embellishment**: "a romantic Italian restaurant", "in the heart of Koreatown", "a calm atmosphere" when the data has no such field. Some flags are judge mistakes (it marked "Sushi World has 4.4/5 on Tripadvisor" as unsupported although the evidence has it). The recommender's free text is the next place to apply the grounding check.

## Hallucination rate (repeated runs)

Questions the guide cannot answer, asked 5 times each through the full system:

| Question | Without check | With check | Answers revised by the check |
|---|---|---|---|
| Does Sakura Garden take reservations? | 20% | 20% | 1 / 5 |
| What time does Iron Griddle open on Sundays? | 0% | 0% | 0 / 5 |
| Is there parking at The Noodle Nest? | **60%** | **0%** | 4 / 5 |
| **Mean** | **27%** | **7%** | |

The grounding check (one extra LLM call on the tool-agent path) removes most invented facts. It is enabled by default. It does not catch everything: one reservations answer in five still claims something the data lacks, so the red team's finding is **mitigated, not closed**.

## Memory and web discovery (2026-10-26, Bedrock Nova Lite)

Added after testing the deployed app: follow-ups lost the place, allergies only counted in the message that said them, and web searches found nothing when Overpass was down. Agent model `us.amazon.nova-lite-v1:0`, guardrails `local`, recorded web data.

**Multi-turn suite** (9 conversations, 22 turns, one signed-in user per conversation):

| Run | Conversations passed | Turn checks passed | What changed before it |
|---|---|---|---|
| v1 | 9 / 9 | 100% | First version of the checks |
| v2 | 8 / 9 | 97.5% | Stricter checks (`understood_excludes`) after reading the v1 answers; rules for price words, vibe length and topic switches |
| v3 | 8 / 9 | 97.5% | Profile prompt: previous picks are only for references, a new topic starts over |
| v4 | **9 / 9** | **100%** | Code rule: after a topic switch, only cuisines the message names count |

v1 passed every check, but its answers showed that **memory made extraction mistakes stick** for the whole conversation: an invented `$$$` budget for "dinner", "noodles" from a recipe question carried into a restaurant search, a whole sentence ("Soy alérgico al maní.") as the occasion, and a previous card's cuisine copied into the profile. The checks were extended to catch them, then fixed: rules in `memory.merge_profile` for the first three, the prompt for the fourth. "Noodles" survived the prompt fix (Nova Lite re-read it from the history) and needed the code rule.

**Overpass down** (`e22`, recorded Tavily listings): route `web → web:tavily`, cards ONI Nikkei, Kuro Sushi Bar and WAN Restaurant, every name present in the results. The judge flagged "ONI Nikkei is a sushi restaurant" as unsupported although the listing says "ONI Nikkei - sushi medellin Sushi, Restaurant": a judge miss, the same kind seen in the calibration.

## Model choice and the hybrid orchestrator (2026-10-27, Bedrock)

Added after production logs showed the agent inventing dishes and re-running a full search for "¿cómo preparo la segunda?". Guardrails off, recorded web data (`evals/fixtures.py`).

**Profile suite, 30 cases, five cheap Bedrock models** (us-east-1 on-demand prices per million tokens):

| Model | All fields correct | Median per call | Input / output price |
|---|---|---|---|
| Nova Lite (deployed) | 90% (misses "near me" / "around here") | 1.4 s | $0.06 / $0.24 |
| **Nova 2 Lite** | **100%** | 1.6 s | $0.33 / $2.75 |
| **gpt-oss-120b** | **100%** | 2.5 s | $0.15 / $0.60 |
| Qwen3 32B | 97% | 1.4 s | $0.15 / $0.60 |
| Ministral 3 14B | 97%, but no answer parsed as structured output | 1.3 s | $0.20 / $0.20 |

Measured traffic is ~1,400 input and ~110 output tokens per call and about 6 calls per conversation, so every candidate costs well under $1 a month at demo traffic. Nova 2 Lite and gpt-oss-120b both passed the multi-turn suite; Nova 2 Lite was twice as fast per conversation.

**Fixed routing vs hybrid orchestrator, multi-turn suite (14 conversations, Nova 2 Lite):**

| Mode | Conversations passed | Median time per conversation |
|---|---|---|
| Fixed routing (`ORCHESTRATOR_ENABLED=false`) | 13 / 14 | 7.2 s |
| Orchestrator, model asked again after every tool | 14 / 14 | 17.0 s |
| **Orchestrator, plan then execute** | **14 / 14** | **9.2 s** |

The fixed graph cannot serve a compound request (`m14`: "a moody Italian spot in Pasadena and what to order at the first one"); the orchestrator chains `search_restaurants` and `place_info`. The first version asked the model again after the tools only to hear it was done, which doubled the time; the model now plans its calls once, and gets another round only to fix an error or when the message asks for several things and only one call came (Nova 2 Lite plans one call at a time). With Nova Lite and the fixed routing (the deployed configuration) the suite passes 12 / 14: `m14` and `m06`, an ambiguous "recomiéndame algo para cenar" (recipe or restaurant).

What the runs caught, all fixed with a test:
- **Wrong place for a position.** The orchestrator passed "Sushi World" for "¿qué pido en el segundo?". Positions are now resolved in code; the model's name is ignored when the user gave a position and did not write that name.
- **A search read as a name.** "Sushi en Laureles, Medellín" became a lookup of a restaurant called "Sushi en Laureles" ("its name suggests…"). A "name" made of the requested cuisine and a place is now a search, and the neighbourhood inside it is kept.
- **A follow-up read as a search.** Nova Lite ran a new search for "háblame más del segundo"; with picks shown and no new place or cuisine in the message, a position means a follow-up.
- **Unrequested work.** The orchestrator added the steps of the first pasta to a plain "dame una receta de pasta" list and repeated a web recipe; steps are fetched in the same turn only when the user asks how to cook.
- **Allergy wording.** Nova 2 Lite called a dish "segura" for a peanut allergy; the safety-claim filter now removes bare "segura" / "safe for" sentences.

## Bugs the evaluations found in the product

- **Allergy safety gap.** The recommender wrote "Thai Basil Chicken is a *peanut-free option*". The safety filter only removed "is peanut-free" without an article. Fixed in `security/allergens.py`, with a test.
- **Off-topic misrouting.** Coding requests were classified as database requests. Fixed in the profile prompt.
- **Shared eval session.** Every end-to-end case used the session `evals`; with conversation memory, one case would inherit the place or allergies of the previous one. Each case now gets its own session.
- **Sticky extraction mistakes.** See the multi-turn section: invented budgets, topic leaks and copied sentences, fixed with rules and checked by the suite.

## Next steps

1. Re-run every suite with Nova Lite (the multi-turn suite already runs on it) and a stronger judge, and compare against `llama3.1`.
2. Apply the grounding check to the recommender's free text.
3. Add BM25 hybrid search and more visual queries, then revisit the fusion weight.
