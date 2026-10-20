"""Pattern rules for prompt injection in English and Spanish.

They complement Prompt Guard 2 (22M), which in our tests missed Spanish injections and indirect
injections phrased as instructions to "the assistant" inside data (e.g. a fake "system note" in a
review). Rules are cheap, explainable and easy to extend; the classifier catches paraphrases the
rules do not. Neither is perfect, which is why untrusted content is also isolated in prompts.
"""
import re

_FLAGS = re.IGNORECASE | re.DOTALL

INJECTION_RULES: dict[str, re.Pattern] = {
    "override_en": re.compile(
        r"\b(ignore|disregard|forget|override)\b.{0,40}\b(previous|prior|above|earlier|all|your)\b.{0,25}"
        r"\b(instructions?|rules|prompts?|guidelines)\b", _FLAGS),
    "override_es": re.compile(
        r"\b(ignora|olvida|omite|descarta|anula)\w*\b.{0,40}\b(instrucciones|reglas|indicaciones|prompt)\b",
        _FLAGS),
    "system_prompt": re.compile(
        r"\b(system|developer)\s*(prompt|message|note|instruction)s?\b"
        r"|\b(prompt|mensaje|nota|instrucci[oó]n)(es)?\s+(de|del)\s+(sistema|desarrollador)\b", _FLAGS),
    "role_hijack": re.compile(
        r"\byou are now\b|\bfrom now on you\b|\bahora eres\b|\ba partir de ahora (eres|act[uú]a)\b"
        r"|\b(developer|god|dan)\s+mode\b|\bmodo (desarrollador|dios)\b|\bjailbreak\b", _FLAGS),
    "instructions_to_assistant": re.compile(
        r"\b(assistant|ai|chatbot|model|asistente|ia)\b.{0,30}\b(must|should|needs? to|debe|tiene que)\b.{0,80}"
        r"\b(tell|say|visit|enter|send|recommend|di|dile|visita|ingresa|env[ií]a|recomienda)\b", _FLAGS),
    "exfiltration": re.compile(
        r"\b(reveal|print|show|repeat|leak|muestra|revela|imprime|repite)\b.{0,30}"
        r"\b(system prompt|your (instructions|prompt)|tus instrucciones|tu prompt|secrets?|secretos|api ?keys?)\b",
        _FLAGS),
    "credential_phishing": re.compile(
        r"\b(enter|type|send|share|ingresa|escribe|env[ií]a|comparte)\b.{0,40}"
        r"\b(card number|credit card|password|contrase[nñ]a|tarjeta|cvv|pin)\b", _FLAGS),
}

RULE_SCORE = 0.9


def matched_rules(text: str) -> list[str]:
    return [name for name, pattern in INJECTION_RULES.items() if pattern.search(text)]


def heuristic_score(text: str) -> tuple[float, list[str]]:
    rules = matched_rules(text)
    return (RULE_SCORE if rules else 0.0), rules
