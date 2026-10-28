"""Run the evaluation suites and write a JSON result plus a Markdown summary.

Usage:
  python -m evals.run --suite retrieval                       # deterministic, ~1 min
  python -m evals.run --suite profile --limit 10              # LLM
  python -m evals.run --suite e2e --tag baseline               # LLM + judge, offline web fixtures
  python -m evals.run --suite e2e --grounding-check --tag grounding
  python -m evals.run --suite all --langfuse                  # also record a Langfuse experiment

Results go to evals/results/<tag>.json and evals/results/<tag>.md.
"""
import argparse
import asyncio
import json
import re
import sys
import time
import unicodedata
import uuid
from datetime import UTC, datetime
from pathlib import Path

from connoisseur import memory
from connoisseur.agents import nodes
from connoisseur.agents.prompts import load_prompt
from connoisseur.agents.runner import run_chat
from connoisseur.observability import get_langfuse
from connoisseur.schemas import Coordinates, ProfileExtraction
from connoisseur.settings import get_settings
from evals import judge, retrieval
from evals.fixtures import discovery_text, offline_web
from evals.metrics import mean

ROOT = Path(__file__).parent
UNKNOWN_RE = re.compile(
    r"(?i)\b(don't|do not|doesn't|does not) have (any |that |this )?(access to |details on )?"
    r"(information|info|details|data|reservation|opening|hours|contact)"
    r"|\bno (information|info|details|data)\b|\bnot (available|provided|mentioned|listed)\b"
    r"|\bno (tengo|dispongo de|hay) (informaci[oó]n|datos)"
)
RESULTS = ROOT / "results"


def load_jsonl(name: str) -> list[dict]:
    lines = (ROOT / "datasets" / name).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def fold(text: str | None) -> str:
    text = (text or "").lower()
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


# ---------------- profile suite ----------------

def check_profile(ctx, expected: dict) -> dict[str, bool]:
    checks = {"intent": ctx.intent == expected["intent"]}
    if "language" in expected:
        checks["language"] = fold(ctx.language) == fold(expected["language"])
    if "near_me" in expected:
        checks["near_me"] = ctx.near_me == expected["near_me"]
    if "location_contains" in expected:
        want = expected["location_contains"]
        checks["location"] = ctx.location is None if want is None else fold(want) in fold(ctx.location)
    if "cuisines_any" in expected:
        got = fold(" ".join(ctx.cuisines + [ctx.search_query]))
        checks["cuisines"] = any(fold(c) in got for c in expected["cuisines_any"])
    if "dietary_any" in expected:
        got = fold(" ".join(ctx.dietary + [ctx.search_query]))
        checks["dietary"] = any(fold(d) in got for d in expected["dietary_any"])
    if "restaurant_contains" in expected:
        checks["restaurant"] = fold(expected["restaurant_contains"]) in fold(ctx.restaurant_name)
    return checks


async def profile_case(case: dict) -> dict:
    start = time.perf_counter()
    extracted = await nodes.structured_call(ProfileExtraction, load_prompt("profile"), f"user: {case['message']}")
    ctx = extracted.to_context() if extracted else None
    checks = check_profile(ctx, case["expected"]) if ctx else {"parsed": False}
    return {
        "id": case["id"],
        "message": case["message"],
        "checks": checks,
        "passed": all(checks.values()),
        "got": ctx.model_dump() if ctx else None,
        "seconds": round(time.perf_counter() - start, 2),
    }


def summarize_profile(rows: list[dict]) -> dict:
    fields: dict[str, list[float]] = {}
    for row in rows:
        for name, ok in row["checks"].items():
            fields.setdefault(name, []).append(1.0 if ok else 0.0)
    return {
        "n": len(rows),
        "all_fields_correct": mean([1.0 if r["passed"] else 0.0 for r in rows]),
        "field_accuracy": {k: mean(v) for k, v in sorted(fields.items())},
        "failures": [{"id": r["id"], "message": r["message"],
                      "wrong": [k for k, ok in r["checks"].items() if not ok]} for r in rows if not r["passed"]],
    }


# ---------------- end-to-end suite ----------------

def check_e2e(result: dict, checks: dict) -> dict[str, bool]:
    route, answer, cards = result.get("route", []), result.get("answer", ""), result.get("cards", [])
    out: dict[str, bool] = {}
    if "route_includes" in checks:
        out["route_includes"] = all(r in route for r in checks["route_includes"])
    if "route_any" in checks:
        out["route_any"] = any(r in route for r in checks["route_any"])
    if "route_any_prefix" in checks:
        out["route_any_prefix"] = any(r.startswith(p) for r in route for p in checks["route_any_prefix"])
    if "tools_any" in checks:
        out["tools"] = any(f"tool:{t}" in route for t in checks["tools_any"])
    if "cards_min" in checks:
        out["cards_min"] = len(cards) >= checks["cards_min"]
    if "cards_source" in checks:
        out["cards_source"] = bool(cards) and all(
            c.get("source") == checks["cards_source"] for c in cards if c.get("type") == "restaurant")
    if checks.get("cards_named_in_web_results"):
        # Web places come from an LLM reading search results: each name must be in those results
        web_text = fold(discovery_text())
        out["cards_named_in_web_results"] = bool(cards) and all(
            fold(c["name"]) in web_text for c in cards if c.get("source") == "web")
    if "answer_regex" in checks:
        out["answer_regex"] = all(re.search(p, answer) for p in checks["answer_regex"])
    if "answer_not_regex" in checks:
        out["answer_not_regex"] = not any(re.search(p, answer) for p in checks["answer_not_regex"])
    if "answer_must_not_claim" in checks:
        # "I don't have information on whether it takes reservations" mentions the forbidden phrase
        # without claiming it: only fail when the phrase appears and the answer does not say it is unknown
        claims = any(re.search(p, answer) for p in checks["answer_must_not_claim"])
        out["answer_must_not_claim"] = not claims or bool(UNKNOWN_RE.search(answer))
    return out


async def e2e_case(case: dict, judge_model: str | None) -> dict:
    coords = Coordinates(**case["coords"]) if case.get("coords") else None
    start = time.perf_counter()
    # One conversation per case: with conversation memory, a shared session leaked places and allergies
    with offline_web(overpass_down=bool(case.get("overpass_down"))):
        result = await run_chat(case["message"], [], coords, session_id=f"evals-{case['id']}-{uuid.uuid4().hex[:8]}")
    checks = check_e2e(result, case["checks"])
    row = {
        "id": case["id"],
        "category": case["category"],
        "message": case["message"],
        "route": result.get("route", []),
        "answer": result.get("answer", ""),
        "checks": checks,
        "passed": all(checks.values()),
        "seconds": round(time.perf_counter() - start, 2),
        "trace_id": result.get("trace_id"),
        "evidence": [e[:3000] for e in result.get("evidence", [])],  # kept so results can be re-judged
        "cards": result.get("cards", []),
        "judge": bool(case.get("judge")),
    }
    row["llm_written"] = any(r.startswith("tool:") for r in row["route"]) or bool(result.get("cards"))
    await judge_row(row, judge_model)
    return row


async def judge_row(row: dict, judge_model: str | None) -> None:
    if row.get("judge") and row.get("llm_written"):
        row["groundedness"] = await judge.groundedness(row["message"], row["answer"], row["evidence"], judge_model)
        row["relevance"] = await judge.relevance(row["message"], row["answer"], judge_model)


async def rejudge(rows: list[dict], judge_model: str | None) -> None:
    """Re-score saved answers with the current judge, without running the agents again."""
    for i, row in enumerate(rows, 1):
        await judge_row(row, judge_model)
        g = row.get("groundedness")
        print(f"  [{i}/{len(rows)}] {row['id']} groundedness={g['score'] if g else '-'}", flush=True)


def summarize_e2e(rows: list[dict]) -> dict:
    by_category: dict[str, list[float]] = {}
    for r in rows:
        by_category.setdefault(r["category"], []).append(1.0 if r["passed"] else 0.0)
    grounded = [r["groundedness"] for r in rows if r.get("groundedness")]
    relevance = [r["relevance"]["score"] for r in rows if r.get("relevance")]
    tool_rows = [r for r in rows if "tools" in r["checks"]]
    return {
        "n": len(rows),
        "checks_passed": mean([1.0 if r["passed"] else 0.0 for r in rows]),
        "by_category": {k: mean(v) for k, v in sorted(by_category.items())},
        "tool_selection_accuracy": mean([1.0 if r["checks"]["tools"] else 0.0 for r in tool_rows]),
        "groundedness_mean": mean([g["score"] for g in grounded]),
        "fully_grounded_answers": mean([1.0 if g["grounded"] else 0.0 for g in grounded]),
        "judged_answers": len(grounded),
        "relevance_mean_1_to_5": mean([float(x) for x in relevance]),
        "median_seconds": sorted(r["seconds"] for r in rows)[len(rows) // 2] if rows else 0,
        "failures": [{"id": r["id"], "message": r["message"], "route": r["route"],
                      "failed": [k for k, ok in r["checks"].items() if not ok]} for r in rows if not r["passed"]],
        "unsupported_claims": [{"id": r["id"], "claims": r["groundedness"]["unsupported_claims"]}
                               for r in rows if r.get("groundedness") and not r["groundedness"]["grounded"]],
    }


# ---------------- multi-turn suite (conversation and user memory) ----------------

def check_turn(result: dict, checks: dict, previous: list[dict]) -> dict[str, bool]:
    """Checks of one turn. `previous` holds the results of the earlier turns of the same case."""
    mem = result.get("memory") or {}
    out = check_e2e(result, {k: v for k, v in checks.items() if not k.startswith(("memory_", "understood_"))})
    if "route_excludes" in checks:
        out["route_excludes"] = not any(r in result.get("route", []) for r in checks["route_excludes"])
    if "memory_location_contains" in checks:
        out["memory_location"] = fold(checks["memory_location_contains"]) in fold(mem.get("location"))
    if "memory_allergies_include" in checks:
        out["memory_allergies"] = set(checks["memory_allergies_include"]) <= set(mem.get("allergies") or [])
    if "memory_dietary_include" in checks:
        diet = {fold(d) for d in mem.get("dietary") or []}
        out["memory_dietary"] = all(fold(d) in diet for d in checks["memory_dietary_include"])
    if "memory_dietary_exclude" in checks:
        diet = {fold(d) for d in mem.get("dietary") or []}
        out["memory_dietary_dropped"] = not any(fold(d) in diet for d in checks["memory_dietary_exclude"])
    if "memory_from_saved" in checks:
        out["memory_from_saved"] = bool(mem.get("from_saved")) == checks["memory_from_saved"]
    if checks.get("memory_empty"):
        out["memory_empty"] = not mem.get("allergies") and not mem.get("dietary") and not mem.get("from_saved")
    if "understood_includes" in checks:
        understood = {fold(u) for u in result.get("understood") or []}
        out["understood"] = all(fold(u) in understood for u in checks["understood_includes"])
    if "understood_excludes" in checks:  # values that must not stick: an invented budget, the last topic
        understood = {fold(u) for u in result.get("understood") or []}
        out["understood_clean"] = not any(fold(u) in understood for u in checks["understood_excludes"])
    if "answer_mentions_card" in checks:
        ref = checks["answer_mentions_card"]
        earlier = previous[ref["turn"] - 1].get("cards", []) if len(previous) >= ref["turn"] else []
        picks = [c for c in earlier if c.get("type") == ref.get("type", "restaurant")]
        name = picks[ref["n"] - 1]["name"] if len(picks) >= ref["n"] else None
        out["answer_mentions_card"] = bool(name) and fold(name) in fold(result.get("answer"))
    return out


async def multiturn_case(case: dict) -> dict:
    """Play a conversation turn by turn, sending the history the way the web app does."""
    run_id = f"evals-{case['id']}-{uuid.uuid4().hex[:8]}"
    user = f"user:{run_id}"  # signed-in: memory across chats applies
    chat = 1
    session, history, previous, turns = f"{run_id}-1", [], [], []
    start = time.perf_counter()
    for i, turn in enumerate(case["turns"], 1):
        if turn.get("forget"):
            memory.forget(user, session)
        if turn.get("new_chat") or turn.get("forget"):
            chat += 1
            session, history = f"{run_id}-{chat}", []
        result = await run_chat(turn["message"], history, None, session_id=session, user_id=user)
        checks = check_turn(result, turn.get("checks", {}), previous)
        turns.append({"turn": i, "message": turn["message"], "route": result.get("route", []),
                      "memory": result.get("memory"), "understood": result.get("understood"),
                      "cards": [c.get("name") for c in result.get("cards", [])],
                      "answer": result.get("answer", ""), "checks": checks, "passed": all(checks.values())})
        previous.append(result)
        history += [{"role": "user", "content": turn["message"]},
                    {"role": "assistant", "content": result.get("answer", "")}]
    return {
        "id": case["id"], "category": case["category"], "message": case["turns"][0]["message"],
        "turns": turns, "route": turns[-1]["route"],
        "checks": {f"t{t['turn']}.{k}": ok for t in turns for k, ok in t["checks"].items()},
        "passed": all(t["passed"] for t in turns), "seconds": round(time.perf_counter() - start, 2),
    }


def summarize_multiturn(rows: list[dict]) -> dict:
    by_category: dict[str, list[float]] = {}
    for r in rows:
        by_category.setdefault(r["category"], []).append(1.0 if r["passed"] else 0.0)
    checks = [ok for r in rows for ok in r["checks"].values()]
    return {
        "n": len(rows),
        "conversations_passed": mean([1.0 if r["passed"] else 0.0 for r in rows]),
        "turn_checks_passed": mean([1.0 if ok else 0.0 for ok in checks]),
        "by_category": {k: mean(v) for k, v in sorted(by_category.items())},
        "failures": [{"id": r["id"], "turn": t["turn"], "message": t["message"], "route": t["route"],
                      "memory": t["memory"], "failed": [k for k, ok in t["checks"].items() if not ok]}
                     for r in rows for t in r["turns"] if not t["passed"]],
    }


# ---------------- judge calibration ----------------

async def judge_case(case: dict, judge_model: str | None) -> dict:
    start = time.perf_counter()
    verdict = await judge.groundedness(case["question"], case["answer"], case["evidence"], judge_model)
    predicted = verdict["grounded"] if verdict else None
    return {
        "id": case["id"], "message": case["answer"], "expected_grounded": case["grounded"],
        "predicted_grounded": predicted, "unsupported_claims": (verdict or {}).get("unsupported_claims"),
        "checks": {"agrees_with_label": predicted == case["grounded"]},
        "passed": predicted == case["grounded"], "seconds": round(time.perf_counter() - start, 2),
    }


def summarize_judge(rows: list[dict]) -> dict:
    return {
        "n": len(rows),
        "agreement": mean([1.0 if r["passed"] else 0.0 for r in rows]),
        "false_pass": sum(1 for r in rows if r["predicted_grounded"] is True and not r["expected_grounded"]),
        "false_fail": sum(1 for r in rows if r["predicted_grounded"] is False and r["expected_grounded"]),
        "no_verdict": sum(1 for r in rows if r["predicted_grounded"] is None),
        "disagreements": [{"id": r["id"], "expected": r["expected_grounded"], "got": r["predicted_grounded"],
                           "claims": r["unsupported_claims"]} for r in rows if not r["passed"]],
    }


# ---------------- orchestration ----------------

async def run_suite(name: str, cases: list[dict], judge_model: str | None) -> list[dict]:
    rows = []
    for i, case in enumerate(cases, 1):
        if name == "profile":
            row = await profile_case(case)
        elif name == "judge":
            row = await judge_case(case, judge_model)
        elif name == "multiturn":
            row = await multiturn_case(case)
        else:
            row = await e2e_case(case, judge_model)
        status = "PASS" if row["passed"] else "FAIL"
        print(f"  [{i}/{len(cases)}] {status} {row['id']} ({row['seconds']}s) {row['message'][:60]}", flush=True)
        rows.append(row)
    return rows


def record_langfuse(name: str, tag: str, rows: list[dict]) -> str | None:
    """Store each case as a dataset item and its result as scores on its trace (Langfuse Datasets)."""
    lf = get_langfuse()
    if lf is None:
        return None
    dataset = f"connoisseur-{name}"
    try:
        lf.create_dataset(name=dataset)
    except Exception as exc:  # noqa: BLE001 - usually "already exists"
        print(f"  dataset {dataset}: {str(exc)[:80]}")
    for row in rows:
        lf.create_dataset_item(dataset_name=dataset, id=f"{name}-{row['id']}", input={"message": row["message"]},
                               metadata={"category": row.get("category", name)})
        if row.get("trace_id"):
            lf.create_score(trace_id=row["trace_id"], name="eval_checks_passed", value=1.0 if row["passed"] else 0.0,
                            comment=f"{tag}: {[k for k, ok in row['checks'].items() if not ok]}")
            if row.get("groundedness"):
                lf.create_score(trace_id=row["trace_id"], name="eval_groundedness",
                                value=row["groundedness"]["score"], comment=tag)
    lf.flush()
    return dataset


def fmt(value) -> str:
    return "n/a" if value is None else str(value)


def to_markdown(report: dict) -> str:
    lines = [f"# Evaluation run `{report['tag']}`", "",
             f"- Date: {report['date']}", f"- LLM: `{report['llm']}` · judge: `{report['judge_model']}`",
             (f"- Guardrails: `{report['guardrails']}` · grounding check: `{report['grounding_check']}` · "
              f"expert agents: `{report['expert_analysis']}` · "
              f"orchestrator: `{report.get('orchestrator', False)}`"), ""]
    if "retrieval" in report:
        lines += ["## Retrieval (known-item search, no LLM)", "", "| Query set | n | hit@1 | hit@5 | MRR@10 |",
                  "|---|---|---|---|---|"]
        for name, m in report["retrieval"]["metrics"].items():
            lines.append(f"| {name} | {m['n']} | {m['hit@1']} | {m['hit@5']} | {m['mrr@10']} |")
        lines += ["", "Fusion weight sweep (MRR@10):", "", "| image weight | ingredients | dish name | visual description |",
                  "|---|---|---|---|"]
        for row in report["retrieval"]["fusion_weight_sweep"]:
            lines.append(f"| {row['w_img']} | {row['mrr_ingredients']} | {row['mrr_dish_name']} | "
                         f"{row['mrr_visual_description']} |")
        lines.append("")
    if "judge" in report:
        j = report["judge"]["summary"]
        lines += ["## Judge calibration (hand-labelled groundedness)", "",
                  (f"Agreement with labels: **{fmt(j['agreement'])}** (n={j['n']}) · false pass: {j['false_pass']} · "
                  f"false fail: {j['false_fail']} · no verdict: {j['no_verdict']}"), ""]
        lines += [f"- `{d['id']}` expected grounded={d['expected']}, judge said {d['got']}: {d['claims']}"
                  for d in j["disagreements"]]
        lines.append("")
    if "profile" in report:
        p = report["profile"]["summary"]
        lines += ["## Profile extraction", "", f"All fields correct: **{p['all_fields_correct']}** (n={p['n']})", "",
                  "| Field | Accuracy |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in p["field_accuracy"].items()]
        if p["failures"]:
            lines += ["", "Failures:"] + [f"- `{f['id']}` {f['message']} → wrong: {', '.join(f['wrong'])}"
                                          for f in p["failures"]]
        lines.append("")
    if "e2e" in report:
        e = report["e2e"]["summary"]
        lines += ["## End to end", "", "| Metric | Value |", "|---|---|",
                  f"| Cases | {e['n']} |", f"| Deterministic checks passed | {e['checks_passed']} |",
                  f"| Tool selection accuracy | {fmt(e['tool_selection_accuracy'])} |",
                  f"| Groundedness (judge, mean) | {fmt(e['groundedness_mean'])} |",
                  f"| Fully grounded answers | {fmt(e['fully_grounded_answers'])} ({e['judged_answers']} judged) |",
                  f"| Relevance (judge, 1-5) | {fmt(e['relevance_mean_1_to_5'])} |",
                  f"| Median latency (s) | {e['median_seconds']} |", "", "| Category | Passed |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in e["by_category"].items()]
        if e["failures"]:
            lines += ["", "Failed checks:"] + [f"- `{f['id']}` {f['message']} → {', '.join(f['failed'])} "
                                               f"(route: {' → '.join(f['route'])})" for f in e["failures"]]
        if e["unsupported_claims"]:
            lines += ["", "Unsupported claims found by the judge:"] + [
                f"- `{u['id']}`: " + "; ".join(u["claims"]) for u in e["unsupported_claims"]]
        lines.append("")
    if "multiturn" in report:
        m = report["multiturn"]["summary"]
        lines += ["## Multi-turn conversations (memory)", "", "| Metric | Value |", "|---|---|",
                  f"| Conversations | {m['n']} |",
                  f"| Conversations with every check passed | {m['conversations_passed']} |",
                  f"| Turn checks passed | {m['turn_checks_passed']} |", "",
                  "| Category | Passed |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in m["by_category"].items()]
        if m["failures"]:
            lines += ["", "Failed turns:"] + [
                f"- `{f['id']}` turn {f['turn']} \"{f['message']}\" → {', '.join(f['failed'])} "
                f"(route: {' → '.join(f['route'])}; memory: {f['memory']})" for f in m["failures"]]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--suite", choices=["retrieval", "judge", "profile", "e2e", "multiturn", "all"],
                        default="all")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dataset", default=None, help="override the dataset file name in evals/datasets/")
    parser.add_argument("--tag", default=None)
    parser.add_argument("--judge-model", default=None, help="Ollama model for the judge (default: CHAT_MODEL)")
    parser.add_argument("--grounding-check", action="store_true", help="enable the tool agent grounding check")
    parser.add_argument("--langfuse", action="store_true", help="record results as Langfuse dataset scores")
    parser.add_argument("--rejudge", metavar="TAG", help="re-score the e2e answers saved in evals/results/TAG.json")
    args = parser.parse_args()

    s = get_settings()
    s.enable_grounding_check = args.grounding_check  # explicit, so baseline and treatment runs are comparable
    s.enable_expert_analysis = False  # experts do not change routing or grounding; skipping them saves ~3x time
    s.per_minute_message_limit = s.daily_message_limit = 10_000
    # Evals measure behaviour, not laptop speed: a busy local Ollama made 3 cases hit the 240 s ceiling
    s.request_timeout_s = max(s.request_timeout_s, 600)
    tag = args.tag or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    report: dict = {
        "tag": tag, "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "llm": s.bedrock_model_id if s.llm_provider == "bedrock" else s.chat_model,
        "judge_model": args.judge_model or s.chat_model, "guardrails": s.guardrail_provider,
        "grounding_check": s.enable_grounding_check, "expert_analysis": s.enable_expert_analysis,
        "orchestrator": s.orchestrator_enabled,
    }

    if args.rejudge:
        saved = json.loads((RESULTS / f"{args.rejudge}.json").read_text(encoding="utf-8"))
        rows = saved["e2e"]["rows"]
        print(f"Re-judging {len(rows)} saved answers from {args.rejudge}...")
        asyncio.run(rejudge(rows, args.judge_model))
        cases = {c["id"]: c for c in load_jsonl("e2e.jsonl")}
        for row in rows:  # deterministic checks are re-computed with the current dataset definitions
            if row["id"] in cases:
                new = check_e2e({"route": row["route"], "answer": row["answer"],
                                 "cards": row.get("cards", [])}, cases[row["id"]]["checks"])
                if "cards" not in row:  # saved before cards were stored: keep the original card checks
                    new.update({k: v for k, v in row["checks"].items() if k.startswith("cards")})
                row["checks"] = new
                row["passed"] = all(row["checks"].values())
        saved["e2e"]["summary"] = summarize_e2e(rows)
        saved["judge_model"] = args.judge_model or s.chat_model
        saved["tag"] = args.tag or f"{args.rejudge}-rejudged"
        report = saved
        args.suite = "none"
    if args.suite in ("retrieval", "all"):
        print("Retrieval suite...")
        report["retrieval"] = retrieval.run()
    datasets = {"judge": "judge_calibration.jsonl", "profile": "profile.jsonl", "e2e": "e2e.jsonl",
                "multiturn": "multiturn.jsonl"}
    summarizers = {"judge": summarize_judge, "profile": summarize_profile, "e2e": summarize_e2e,
                   "multiturn": summarize_multiturn}
    for name in ("judge", "profile", "e2e", "multiturn"):
        if args.suite not in (name, "all"):
            continue
        cases = load_jsonl(args.dataset or datasets[name])[: args.limit]
        print(f"{name} suite ({len(cases)} cases)...")
        with offline_web():
            rows = asyncio.run(run_suite(name, cases, args.judge_model))
        summary = summarizers[name](rows)
        report[name] = {"summary": summary, "rows": rows}
        save(report, tag)  # save after every suite: an hour of LLM calls must not be lost to a later error
        if args.langfuse and name != "judge":
            try:
                report[name]["langfuse_dataset"] = record_langfuse(name, tag, rows)
            except Exception as exc:  # noqa: BLE001 - Langfuse down must not fail the evaluation
                print(f"  Langfuse recording skipped: {type(exc).__name__}: {str(exc)[:120]}")

    print("\n" + save(report, tag))
    print(f"Saved evals/results/{tag}.json and .md")


def save(report: dict, tag: str) -> str:
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / f"{tag}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    markdown = to_markdown(report)
    (RESULTS / f"{tag}.md").write_text(markdown, encoding="utf-8")
    return markdown


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # the summary has arrows and accents; a Windows console has neither
    main()
