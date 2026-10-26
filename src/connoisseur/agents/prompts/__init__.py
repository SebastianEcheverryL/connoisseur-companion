"""Agent prompts: versioned Markdown files, optionally served from Langfuse Prompt Management.

With LANGFUSE_PROMPTS=true, `load_prompt("profile")` fetches the prompt `connoisseur/profile` with the
configured label (default "production"), cached for a few minutes. The local file is the fallback, so
the app keeps working when Langfuse is down or the prompt was never published
(`python scripts/sync_prompts.py` publishes the local files).
"""
from functools import lru_cache
from pathlib import Path

from connoisseur.log import get_logger
from connoisseur.observability import get_langfuse
from connoisseur.settings import get_settings

PROMPT_DIR = Path(__file__).parent
PROMPT_NAMES = (
    "profile", "trend_analyst", "style_expert", "nutrition_expert", "recommender", "tool_agent", "grounding_check",
    "web_places",
)
LANGFUSE_PREFIX = "connoisseur/"
CACHE_TTL_S = 300

log = get_logger(__name__)


@lru_cache
def local_prompt(name: str) -> str:
    return (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


def load_prompt(name: str) -> str:
    fallback = local_prompt(name)
    s = get_settings()
    lf = get_langfuse() if s.langfuse_prompts else None
    if lf is None:
        return fallback
    try:
        prompt = lf.get_prompt(
            f"{LANGFUSE_PREFIX}{name}", label=s.langfuse_prompt_label, fallback=fallback,
            cache_ttl_seconds=CACHE_TTL_S,
        )
        return prompt.compile()
    except Exception as exc:  # noqa: BLE001 - any Langfuse failure falls back to the local file
        log.warning("prompt_fetch_failed", prompt=name, error=str(exc)[:200])
        return fallback
