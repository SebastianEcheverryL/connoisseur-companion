"""Publish the local agent prompts (src/connoisseur/agents/prompts/*.md) to Langfuse Prompt Management.

A new version is created only when the text changed. Use --label to choose which label the new
versions get (default: the LANGFUSE_PROMPT_LABEL setting, "production").

Usage: python scripts/sync_prompts.py [--label staging] [--dry-run]
"""
import argparse

from connoisseur.agents.prompts import LANGFUSE_PREFIX, PROMPT_NAMES, local_prompt
from connoisseur.observability import get_langfuse
from connoisseur.settings import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default=get_settings().langfuse_prompt_label)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    lf = get_langfuse()
    if lf is None:
        raise SystemExit("Langfuse is not configured: set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY.")

    for name in PROMPT_NAMES:
        full_name, text = f"{LANGFUSE_PREFIX}{name}", local_prompt(name)
        try:
            current = lf.get_prompt(full_name, label=args.label, cache_ttl_seconds=0).prompt
        except Exception:  # noqa: BLE001 - the prompt does not exist yet
            current = None
        if current == text:
            print(f"unchanged  {full_name}")
            continue
        if args.dry_run:
            print(f"would push {full_name}")
            continue
        lf.create_prompt(name=full_name, prompt=text, labels=[args.label], type="text",
                         tags=["connoisseur-companion"])
        print(f"pushed     {full_name} -> label '{args.label}'")
    lf.flush()


if __name__ == "__main__":
    main()
