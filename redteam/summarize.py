"""Turn a promptfoo results.json into a Markdown report (docs/security/redteam-report.md).

Usage: python redteam/summarize.py redteam/results.json "<stack the suite ran against>" > docs/security/redteam-report.md
"""
import json
import sys
from collections import defaultdict
from datetime import date

DEFAULT_STACK = ("the local API with `GUARDRAIL_PROVIDER=local` (Prompt Guard 2 + injection rules + "
                 "Llama Guard 3 1B) and `llama3.1` as the agent model")


def main(path: str, stack: str = DEFAULT_STACK) -> None:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    rows = data.get("results", {}).get("results", data.get("results", []))

    by_category: dict[str, list[bool]] = defaultdict(list)
    lines = []
    for row in rows:
        test = row.get("testCase", {}) or {}
        category = (test.get("metadata") or {}).get("category", "other")
        passed = bool(row.get("success"))
        by_category[category].append(passed)
        output = (row.get("response") or {}).get("output") or {}
        route = " → ".join(output.get("route", [])) if isinstance(output, dict) else ""
        lines.append(f"| {'✅' if passed else '❌'} | {category} | {test.get('description', '')} | `{route}` |")

    total = sum(len(v) for v in by_category.values())
    passed = sum(sum(v) for v in by_category.values())
    print("# Red-team report\n")
    print(f"Run on {date.today().isoformat()} against {stack}.\n")
    print(f"**{passed} of {total} checks passed.**\n")
    print("| Category | Passed |\n|---|---|")
    for category, results in sorted(by_category.items()):
        print(f"| {category} | {sum(results)}/{len(results)} |")
    print("\n## All tests\n")
    print("| Result | Category | Test | Route taken |\n|---|---|---|---|")
    print("\n".join(lines))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # the report has emoji; the Windows console code page does not
    main(*sys.argv[1:3])
