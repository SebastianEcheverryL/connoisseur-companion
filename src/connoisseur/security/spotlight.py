"""Spotlighting: isolate untrusted text (tool outputs, reviews, web snippets) before an LLM sees it.

The content is screened for prompt injection; if it looks like one it is replaced by a notice.
Otherwise it is wrapped in explicit tags, after removing any look-alike tags an attacker could use
to "close" the block early. Prompts tell the model that tagged text is data, never instructions.
"""
import re

from connoisseur.security.guardrails import check_untrusted

TAG = "untrusted_data"
_TAG_RE = re.compile(r"</?\s*untrusted[\w-]*\s*>", re.IGNORECASE)
REMOVED = "[content removed: it looked like an attempt to give the assistant instructions]"


def sanitize(text: str) -> tuple[str, bool]:
    """Return (safe_text, was_blocked). Blocked content is replaced by a neutral notice."""
    if not check_untrusted(text).allowed:
        return REMOVED, True
    return _TAG_RE.sub("", text), False


def wrap(text: str, source: str) -> str:
    """Screen and wrap untrusted text for inclusion in a prompt."""
    safe, _ = sanitize(text)
    source = re.sub(r"[^\w.:-]", "", source)[:40]
    return f'<{TAG} source="{source}">\n{safe}\n</{TAG}>'
