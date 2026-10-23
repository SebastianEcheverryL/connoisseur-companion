"""Grounding check for free-text answers: remove claims the tool results do not support.

Added after the red team found the tool agent saying Sakura Garden "takes reservations" although the
guide has no reservation data, and a prompt rule alone did not stop it. One extra LLM call compares
the draft answer with the evidence and returns a revised answer without unsupported claims.
"""
from pydantic import BaseModel, Field

from connoisseur.agents.prompts import load_prompt
from connoisseur.log import get_logger
from connoisseur.observability import traced

log = get_logger(__name__)
MAX_EVIDENCE_CHARS = 6000


class GroundingCheck(BaseModel):
    unsupported_claims: list[str] = Field(description="Claims in the draft that the evidence does not support")
    revised_answer: str = Field(description="The draft without unsupported claims")


def _evidence_block(evidence: list[str]) -> str:
    text = "\n\n".join(evidence)
    return text[:MAX_EVIDENCE_CHARS]


@traced("grounding_check", as_type="evaluator")
async def check(question: str, answer: str, evidence: list[str]) -> GroundingCheck | None:
    from connoisseur.agents.nodes import structured_call  # local import: nodes imports this package

    user = f"Question: {question}\n\nDraft answer:\n{answer}\n\nEVIDENCE:\n{_evidence_block(evidence)}"
    return await structured_call(GroundingCheck, load_prompt("grounding_check"), user)


async def enforce(question: str, answer: str, evidence: list[str]) -> tuple[str, bool]:
    """Return (answer, revised). Keeps the draft if the checker fails or returns something unusable."""
    result = await check(question, answer, evidence)
    if result is None or not result.unsupported_claims or not result.revised_answer.strip():
        return answer, False
    log.info("grounding_revised", removed=result.unsupported_claims[:5])
    return result.revised_answer.strip(), True
