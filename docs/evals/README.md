# Evaluations

How Connoisseur Companion is measured, and why each suite exists. Latest numbers: [report.md](report.md).

```bash
python -m evals.run --suite retrieval            # deterministic, ~1 min, no LLM
python -m evals.run --suite judge                # calibrate the LLM judge against hand labels
python -m evals.run --suite profile              # profile/router agent, 30 cases
python -m evals.run --suite e2e [--grounding-check] [--langfuse]   # 22 end-to-end cases + judge
python -m evals.run --suite multiturn            # 9 conversations: memory across turns and chats
python -m evals.run --rejudge <tag>              # re-score saved answers without re-running agents
python -m evals.repeat --n 5                     # hallucination RATE over repeated questions
```

Results are written to `evals/results/<tag>.json` and `.md` (git-ignored). With `--langfuse`, each case's checks and groundedness are attached as scores to its trace.

## Suites

| Suite | What it answers | How | LLM? |
|---|---|---|---|
| **Retrieval** | Does the index find the right item? Does image fusion help? | Known-item queries generated from the dataset (a restaurant's signature dish, a vibe + cuisine, a recipe's ingredients or name) plus 10 hand-written descriptions of how dishes *look*. Metrics: hit@1, hit@5, MRR@10, and a sweep of the image weight. | No |
| **Judge calibration** | Can the LLM judge be trusted? | 12 answer/evidence pairs labelled by hand as grounded or not. Reports agreement, false passes and false fails. | Judge only |
| **Profile** | Does the first agent understand the request? | 30 messages in English and Spanish with expected intent, language, location, "near me", cuisines, diet and restaurant name. | Yes |
| **End to end** | Does the whole system do the right thing? | 22 cases through the real graph with guardrails on and recorded web data (no network): routing, tool choice, cards, refusals, plus judge groundedness and relevance on LLM-written answers. One case runs with Overpass down, so the Tavily place discovery path is measured: every web card must be named in the recorded results. | Yes |
| **Multi-turn** | Does the assistant remember? | 9 conversations of 2 to 4 turns, sent with the history the web app sends, as one signed-in user per case. Each turn checks the remembered place, diet and allergies, the chips, the route and, for "tell me about the second one", that the answer names that earlier pick. Also a new chat that starts from the saved memory, a forgotten one, and a dropped diet. | Yes |
| **Repeat** | How often does it invent facts? | The same 3 questions the guide cannot answer (reservations, hours, parking), 5 times each, with and without the grounding check. | Yes |

## Design decisions

- **Known-item retrieval queries are generated from the data**, so the set is reproducible (fixed seed) and needs no labelling. Their weakness is that they favour text search (the query comes from the text), which is why the hand-written *visual* queries exist.
- **The judge is evaluated before its numbers are used.** A first version scored a correct answer as wrong because it quoted the answer instead of the evidence, ignored negations, and broke on compact JSON. Claims now need a quote that really exists in the evidence (compared after removing quotes, brackets and whitespace), and claims that restate an "I don't have that information" are dropped in code.
- **Deterministic checks first, judge second.** Routing, tools, refusals and forbidden claims are checked with code; the judge is only used where meaning matters (groundedness, relevance).
- **Forbidden-claim checks understand negation.** "I don't have information on whether it takes reservations" contains "takes reservations" but does not claim it; the first regex version failed it.
- **Recorded web data** (`evals/fixtures.py`) makes the web branch repeatable and includes a review with a planted prompt injection that must be dropped.
- **One conversation per case.** Since the backend remembers conversations, every case gets its own session (and, in the multi-turn suite, its own user): a shared session let one case's place or allergy leak into the next.
- **Memory is checked on what the API returns** (`memory`, `understood`, `route`), the same fields the UI shows, rather than on internal state.
- **Rates, not anecdotes.** A sampling LLM can answer correctly once and hallucinate the next time, so hallucination is measured as a rate over repeated runs.
- **Expert agents are disabled in evals**: they do not change routing or grounding and would triple the run time on a laptop.

## Known limitations

- The judge is `llama3.1` 8B (local, free). Its agreement with hand labels varies between 0.83 and 0.92 across runs, and it is stricter with long answers than a human: it flags subjective descriptions ("a hearty option") as unsupported. Treat groundedness as a trend indicator, not an exact score. A stronger judge (for example a Bedrock model in phase 6) would tighten it.
- Datasets are small (30 profile cases, 22 end-to-end, 9 conversations, 10 visual queries). They catch regressions and support the decisions documented here; they are not a benchmark.
