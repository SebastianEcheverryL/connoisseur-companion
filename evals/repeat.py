"""Measure a hallucination RATE by repeating the same question, with and without the grounding check.

One run of a sampling LLM proves nothing: in the red team the tool agent said Sakura Garden "takes
reservations", and in a later run it correctly said it had no information. This script asks the same
questions N times per configuration and counts how often the answer claims facts the guide lacks.

Usage: python -m evals.repeat --n 5
"""
import argparse
import asyncio
import json
import re
from datetime import UTC, datetime

from connoisseur.agents.runner import run_chat
from connoisseur.settings import get_settings
from evals.run import RESULTS, UNKNOWN_RE

QUESTIONS = [
    ("Does Sakura Garden take reservations?",
     [r"(?i)(allows?|accepts?|takes?|offers?)\s+(for\s+)?(online\s+)?reservations", r"(?i)reservations are (available|accepted)"]),
    ("What time does Iron Griddle open on Sundays?", [r"(?i)\b\d{1,2}(:\d{2})?\s?(am|pm)\b"]),
    ("Is there parking at The Noodle Nest?", [r"(?i)\b(has|offers|provides|there is)\b[^.]{0,30}\bparking\b"]),
]


def hallucinated(answer: str, patterns: list[str]) -> bool:
    return any(re.search(p, answer) for p in patterns) and not UNKNOWN_RE.search(answer)


async def measure(n: int, grounding_check: bool) -> dict:
    get_settings().enable_grounding_check = grounding_check
    per_question = {}
    for question, patterns in QUESTIONS:
        flags, revised = [], 0
        for i in range(n):
            result = await run_chat(question, [], None, session_id="evals-repeat")
            flags.append(hallucinated(result.get("answer", ""), patterns))
            revised += "grounding:revised" in result.get("route", [])
            print(f"  grounding={grounding_check} [{i + 1}/{n}] hallucinated={flags[-1]} {question}", flush=True)
        per_question[question] = {"hallucination_rate": round(sum(flags) / n, 3), "revised_by_check": revised}
    total = [q["hallucination_rate"] for q in per_question.values()]
    return {"grounding_check": grounding_check, "n_per_question": n,
            "mean_hallucination_rate": round(sum(total) / len(total), 3), "questions": per_question}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=5)
    args = parser.parse_args()
    s = get_settings()
    s.enable_expert_analysis = False
    s.per_minute_message_limit = s.daily_message_limit = 10_000
    s.request_timeout_s = max(s.request_timeout_s, 600)

    results = [asyncio.run(measure(args.n, False)), asyncio.run(measure(args.n, True))]
    RESULTS.mkdir(exist_ok=True)
    tag = f"repeat-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
    (RESULTS / f"{tag}.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("\n| Question | without check | with check | answers revised |\n|---|---|---|---|")
    for question in results[0]["questions"]:
        a, b = results[0]["questions"][question], results[1]["questions"][question]
        print(f"| {question} | {a['hallucination_rate']} | {b['hallucination_rate']} | {b['revised_by_check']}/{args.n} |")
    print(f"| **Mean** | **{results[0]['mean_hallucination_rate']}** | **{results[1]['mean_hallucination_rate']}** | |")
    print(f"\nSaved evals/results/{tag}.json")


if __name__ == "__main__":
    main()
