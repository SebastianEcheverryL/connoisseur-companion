"""LLM-as-judge for groundedness and relevance, run on a local model (no paid API).

The judge is a separate prompt from the app's own grounding check, and its model is configurable
(`--judge-model`). Because a weak judge makes every metric meaningless, the judge itself is measured
against hand-labelled examples (`python -m evals.run --suite judge`).

Groundedness is judged claim by claim, and a claim only counts as supported when the judge quotes the
evidence that supports it: in a first version, asking for a single verdict let llama3.1 misread
"I don't have that information" as a claim and ignore diet tags present in the evidence.
"""
import re
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from connoisseur.llm import get_chat_model

GROUNDEDNESS_PROMPT = """You check an assistant's ANSWER against the EVIDENCE it was given (tool results or
candidate records, in JSON or text).

1. Extract the factual claims about restaurants, dishes or recipes: names, locations, cuisines, ratings, prices,
   dishes, ingredients, diet options (vegan, vegetarian, gluten-free...), atmosphere, hours, reservations, phone
   numbers, policies.
2. Do NOT extract: greetings, opinions ("a great choice"), suggestions ("check with the staff", "search online"),
   or statements that something is unknown or not available ("I don't have that information").
3. For each claim, set verdict "supported" only if the evidence states it (synonyms and translations count, e.g.
   diet_tags ["vegan"] supports "has vegan options"; "vibe": "intimate" supports "intimate atmosphere").
   Copy the supporting text into `evidence_quote`: it must be copied from the EVIDENCE, never from the ANSWER.
   Otherwise set verdict "unsupported" and leave the quote empty.
4. A sentence saying the assistant does NOT know something ("I don't have information on whether X takes
   reservations") is not a claim that X takes reservations: skip it.
If the answer has no factual claims, return an empty list."""

RELEVANCE_PROMPT = """You grade how well an assistant's answer addresses the user's request.
Score 1-5: 5 = fully addresses the request with useful specifics; 3 = partially; 1 = off-topic or useless.
A correct "I don't have that information" for something truly unavailable deserves at least 4."""


class ClaimVerdict(BaseModel):
    claim: str
    verdict: Literal["supported", "unsupported"]
    evidence_quote: str


class GroundednessVerdict(BaseModel):
    claims: list[ClaimVerdict]


class RelevanceVerdict(BaseModel):
    score: int = Field(ge=1, le=5)
    reason: str


async def _ask(schema, system: str, user: str, model: str | None):
    llm = get_chat_model(model=model, temperature=0).with_structured_output(schema)
    try:
        result = await llm.ainvoke([SystemMessage(content=system), HumanMessage(content=user)])
        return result if isinstance(result, schema) else schema.model_validate(result)
    except Exception:  # noqa: BLE001 - a failed judgement is recorded as missing, not as a pass or fail
        return None


def _normalise(text: str) -> str:
    """Lowercase and drop quotes, braces, brackets and ALL whitespace: judges re-quote JSON in their own style
    ('diet_tags: ["vegan"]' for '"diet_tags": ["vegan"]', single quotes, and "key: value" with a space while
    MCP tools return compact JSON like '"location":"Little Tokyo"', which broke the first baseline run)."""
    return re.sub(r"[\"'{}\[\]\s]", "", text.lower())


def _quote_found(quote: str, evidence_text: str) -> bool:
    """A 'supported' verdict must quote something that really is in the evidence."""
    quote = _normalise(quote)
    return len(quote) >= 3 and quote[:60] in _normalise(evidence_text)


UNKNOWN_SENTENCE = re.compile(
    r"(?i)\b(don't|do not|doesn't|does not) have\b|\bno (information|info|details|data)\b"
    r"|\bnot (available|provided|mentioned|listed)\b|\bno (tengo|dispongo|hay)\b"
)
STOPWORDS = {"the", "a", "an", "is", "are", "has", "have", "does", "do", "of", "in", "on", "for", "and", "it", "its"}


def _restates_unknown(claim: str, answer: str) -> bool:
    """True when the 'claim' is really a sentence where the answer says it does NOT know (a small judge
    turns "I don't have information on whether X takes reservations" into "X takes reservations")."""
    words = {w for w in re.findall(r"[a-záéíóúñ0-9]+", claim.lower()) if w not in STOPWORDS}
    for sentence in re.split(r"(?<=[.!?])\s+", answer):
        if UNKNOWN_SENTENCE.search(sentence):
            overlap = words & set(re.findall(r"[a-záéíóúñ0-9]+", sentence.lower()))
            # Most of the claim must be in that sentence: sharing only the restaurant name is not enough
            if words and len(overlap) / len(words) >= 0.75:
                return True
    return False


async def groundedness(question: str, answer: str, evidence: list[str], model: str | None = None) -> dict | None:
    if not evidence:
        return None
    evidence_text = "\n".join(evidence)[:8000]
    user = f"QUESTION: {question}\n\nANSWER:\n{answer}\n\nEVIDENCE:\n{evidence_text}"
    verdict = await _ask(GroundednessVerdict, GROUNDEDNESS_PROMPT, user, model)
    if verdict is None:
        return None
    claims = [c for c in verdict.claims if not _restates_unknown(c.claim, answer)]
    unsupported = [
        c.claim for c in claims if c.verdict == "unsupported" or not _quote_found(c.evidence_quote, evidence_text)
    ]
    total = len(claims)
    return {
        "score": 1.0 if total == 0 else round(1 - len(unsupported) / total, 3),
        "grounded": not unsupported,
        "claims": total,
        "unsupported_claims": unsupported[:5],
    }


async def relevance(question: str, answer: str, model: str | None = None) -> dict | None:
    verdict = await _ask(RelevanceVerdict, RELEVANCE_PROMPT, f"REQUEST: {question}\n\nANSWER:\n{answer}", model)
    return None if verdict is None else {"score": verdict.score, "reason": verdict.reason[:200]}
