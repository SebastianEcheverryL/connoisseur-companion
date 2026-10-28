"""Input and output guardrails with interchangeable providers.

- local:   Prompt Guard 2 (ONNX) + injection rules + Llama Guard 3 1B via Ollama. Free, runs offline.
- bedrock: Amazon Bedrock Guardrails (ApplyGuardrail API): content filters, prompt attacks, PII, denied topics.
- none:    allow everything (tests, benchmarks).

Every decision is a `GuardrailResult` and is traced as a `guardrail` observation in Langfuse.
"""
import asyncio
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal, Protocol

from connoisseur.log import get_logger
from connoisseur.observability import traced
from connoisseur.security.heuristics import heuristic_score
from connoisseur.security.prompt_guard import get_prompt_guard
from connoisseur.settings import get_settings

log = get_logger(__name__)

Stage = Literal["input", "output", "untrusted_content"]

# Llama Guard 3 hazard taxonomy (MLCommons)
LLAMA_GUARD_CATEGORIES = {
    "S1": "violent crimes", "S2": "non-violent crimes", "S3": "sex-related crimes",
    "S4": "child sexual exploitation", "S5": "defamation", "S6": "specialized advice",
    "S7": "privacy", "S8": "intellectual property", "S9": "indiscriminate weapons", "S10": "hate",
    "S11": "suicide and self-harm", "S12": "sexual content", "S13": "elections", "S14": "code interpreter abuse",
}


@dataclass
class GuardrailResult:
    allowed: bool
    stage: Stage
    category: str | None = None  # e.g. "prompt_injection", "S10 hate"
    score: float = 0.0
    details: list[str] = field(default_factory=list)
    provider: str = "none"

    @classmethod
    def ok(cls, stage: Stage, provider: str, score: float = 0.0) -> "GuardrailResult":
        return cls(allowed=True, stage=stage, score=score, provider=provider)


class Guardrail(Protocol):
    name: str

    async def check_input(self, text: str) -> GuardrailResult: ...
    async def check_output(self, user_text: str, answer: str) -> GuardrailResult: ...
    def check_untrusted(self, text: str) -> GuardrailResult: ...


def _unavailable(stage: Stage, provider: str, what: str) -> GuardrailResult:
    """A guardrail model is down: block or allow depending on GUARDRAIL_FAIL_CLOSED."""
    blocked = get_settings().guardrail_fail_closed
    log.warning("guardrail_unavailable", component=what, fail_closed=blocked)
    return GuardrailResult(allowed=not blocked, stage=stage, category="guardrail_unavailable" if blocked else None,
                           details=[f"{what} unavailable"], provider=provider)


def screen_injection(text: str, stage: Stage, provider: str = "local") -> GuardrailResult:
    """Prompt Guard probability combined with pattern rules (synchronous, ~20 ms)."""
    s = get_settings()
    rule_score, rules = heuristic_score(text)
    guard = get_prompt_guard()
    model_score = guard.malicious_probability(text) if guard else 0.0
    score = max(rule_score, model_score)
    details = [f"rule:{r}" for r in rules] + ([f"prompt_guard:{model_score}"] if guard else ["prompt_guard:unavailable"])
    if score >= s.prompt_injection_threshold:
        return GuardrailResult(allowed=False, stage=stage, category="prompt_injection", score=score,
                               details=details, provider=provider)
    if guard is None and not rules:
        return _unavailable(stage, provider, "prompt_guard")
    return GuardrailResult(allowed=True, stage=stage, score=score, details=details, provider=provider)


def parse_llama_guard(content: str, allowed_categories: list[str]) -> tuple[bool, str | None]:
    """Llama Guard answers "safe" or "unsafe
S1,S10". Categories listed as allowed do not block."""
    verdict = content.strip().split()
    if not verdict or verdict[0].lower() == "safe":
        return True, None
    codes = [c for token in verdict[1:] for c in token.split(",") if c]
    blocking = [c for c in codes if c not in allowed_categories]
    if codes and not blocking:
        return True, None
    code = blocking[0] if blocking else "unsafe"
    return False, f"{code} {LLAMA_GUARD_CATEGORIES.get(code, 'unsafe content')}".strip()


class LocalGuardrail:
    name = "local"

    async def _llama_guard(self, messages: list[dict], allowed: list[str]) -> tuple[bool, str | None] | None:
        """Ask Llama Guard 3 to classify the last turn. Returns (safe, category) or None if unavailable."""
        import ollama

        s = get_settings()
        try:
            client = ollama.AsyncClient(host=s.ollama_base_url, timeout=s.tool_timeout_s)
            response = await client.chat(model=s.llama_guard_model, messages=messages, options={"temperature": 0})
        except Exception as exc:  # noqa: BLE001 - connection refused, model not pulled, timeout...
            log.warning("llama_guard_error", error=str(exc)[:200])
            return None
        return parse_llama_guard(response["message"]["content"], allowed)

    async def check_input(self, text: str) -> GuardrailResult:
        injection = await asyncio.to_thread(screen_injection, text, "input", self.name)
        if not injection.allowed:
            return injection
        verdict = await self._llama_guard([{"role": "user", "content": text}],
                                          get_settings().llama_guard_allowed_input_categories)
        if verdict is None:
            return _unavailable("input", self.name, "llama_guard")
        safe, category = verdict
        if not safe:
            return GuardrailResult(allowed=False, stage="input", category=category, provider=self.name,
                                   details=injection.details)
        return injection

    async def check_output(self, user_text: str, answer: str) -> GuardrailResult:
        """Check the whole answer; if flagged, confirm paragraph by paragraph before blocking.

        In the red team, Llama Guard 3 1B flagged a plain three-recipe answer as "S4" while every
        paragraph on its own was safe: the small model degrades on long inputs. Harmful content is
        still caught because it is harmful in the paragraph that contains it.
        """
        allowed = get_settings().llama_guard_allowed_output_categories

        def turn(text: str) -> list[dict]:
            return [{"role": "user", "content": user_text}, {"role": "assistant", "content": text}]

        verdict = await self._llama_guard(turn(answer), allowed)
        if verdict is None:
            return _unavailable("output", self.name, "llama_guard")
        if verdict[0]:
            return GuardrailResult.ok("output", self.name)

        paragraphs = [p.strip() for p in re.split(r"\n\s*\n|\n(?=\d+\.\s)", answer) if p.strip()]
        for paragraph in paragraphs if len(paragraphs) > 1 else []:
            chunk = await self._llama_guard(turn(paragraph), allowed)
            if chunk is not None and not chunk[0]:
                return GuardrailResult(allowed=False, stage="output", category=chunk[1], provider=self.name,
                                       details=[f"paragraph: {paragraph[:80]}"])
        if len(paragraphs) > 1:
            log.info("output_guard_overruled", whole_answer_category=verdict[1], paragraphs=len(paragraphs))
            return GuardrailResult(allowed=True, stage="output", provider=self.name,
                                   details=[f"whole answer flagged {verdict[1]}, no paragraph flagged"])
        return GuardrailResult(allowed=False, stage="output", category=verdict[1], provider=self.name)

    def check_untrusted(self, text: str) -> GuardrailResult:
        return screen_injection(text, "untrusted_content", self.name)


class BedrockGuardrail:
    """Amazon Bedrock Guardrails. The guardrail itself (filters, PII, denied topics) is defined in Terraform."""

    name = "bedrock"

    def __init__(self):
        import boto3

        s = get_settings()
        if not s.bedrock_guardrail_id:
            raise ValueError("GUARDRAIL_PROVIDER=bedrock needs BEDROCK_GUARDRAIL_ID")
        self.client = boto3.client("bedrock-runtime", region_name=s.aws_region)
        self.guardrail_id, self.version = s.bedrock_guardrail_id, s.bedrock_guardrail_version

    async def _apply(self, text: str, source: Literal["INPUT", "OUTPUT"], stage: Stage) -> GuardrailResult:
        try:
            resp = await asyncio.to_thread(
                self.client.apply_guardrail,
                guardrailIdentifier=self.guardrail_id,
                guardrailVersion=self.version,
                source=source,
                content=[{"text": {"text": text}}],
            )
        except Exception as exc:  # noqa: BLE001 - throttling, permissions, network
            log.warning("bedrock_guardrail_error", error=str(exc)[:200])
            return _unavailable(stage, self.name, "bedrock_guardrail")
        if resp.get("action") != "GUARDRAIL_INTERVENED":
            return GuardrailResult.ok(stage, self.name)
        reasons = [
            f"{policy}:{item.get('type') or item.get('name')}"
            for assessment in resp.get("assessments", [])
            for policy, block in assessment.items()
            if isinstance(block, dict)
            for items in block.values()
            if isinstance(items, list)
            for item in items
            if isinstance(item, dict) and item.get("action") in ("BLOCKED", "ANONYMIZED")
        ]
        return GuardrailResult(allowed=False, stage=stage, category=reasons[0] if reasons else "bedrock_guardrail",
                               details=reasons, provider=self.name)

    async def check_input(self, text: str) -> GuardrailResult:
        # Bedrock's prompt-attack filter let "Olvida todas tus instrucciones anteriores..." through (red team,
        # 2026-10-28): the local screen runs first at no cost. Only its detections count here; a missing
        # Prompt Guard leaves the decision to Bedrock instead of failing every message closed
        screened = screen_injection(text, "input", self.name)
        if screened.category == "prompt_injection":
            return screened
        return await self._apply(text, "INPUT", "input")

    async def check_output(self, user_text: str, answer: str) -> GuardrailResult:
        return await self._apply(answer, "OUTPUT", "output")

    def check_untrusted(self, text: str) -> GuardrailResult:
        # Screening every snippet through Bedrock would multiply cost: use the local ONNX screen here too
        return screen_injection(text, "untrusted_content", self.name)


class NoGuardrail:
    name = "none"

    async def check_input(self, text: str) -> GuardrailResult:
        return GuardrailResult.ok("input", self.name)

    async def check_output(self, user_text: str, answer: str) -> GuardrailResult:
        return GuardrailResult.ok("output", self.name)

    def check_untrusted(self, text: str) -> GuardrailResult:
        return GuardrailResult.ok("untrusted_content", self.name)


@lru_cache
def get_guardrail() -> Guardrail:
    provider = get_settings().guardrail_provider
    if provider == "bedrock":
        return BedrockGuardrail()
    if provider == "local":
        return LocalGuardrail()
    return NoGuardrail()


@traced("input_guardrail", as_type="guardrail")
async def check_input(text: str) -> GuardrailResult:
    result = await get_guardrail().check_input(text)
    _log(result)
    return result


@traced("output_guardrail", as_type="guardrail")
async def check_output(user_text: str, answer: str) -> GuardrailResult:
    result = await get_guardrail().check_output(user_text, answer)
    _log(result)
    return result


def check_untrusted(text: str) -> GuardrailResult:
    result = get_guardrail().check_untrusted(text)
    if not result.allowed:
        _log(result)
    return result


def _log(result: GuardrailResult) -> None:
    if not result.allowed:
        log.warning("guardrail_blocked", stage=result.stage, category=result.category, score=result.score,
                    details=result.details, provider=result.provider)
