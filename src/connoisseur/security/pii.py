"""Regex detectors for personal data and secrets, shared by trace masking and output sanitising."""
import re

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
COORDS_RE = re.compile(r"-?\d{1,2}\.\d{2,}\s*,\s*-?\d{1,3}\.\d{2,}")
SECRET_RE = re.compile(r"\b(?:tvly-|sk-lf-|pk-lf-|AKIA|ghp_|sk-)[\w-]{8,}")
PHONE_CANDIDATE_RE = re.compile(r"(?<![\w.])\+?\d[\d\s().-]{8,}\d(?![\w.])")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def _luhn_ok(number: str) -> bool:
    digits = [int(d) for d in number if d.isdigit()]
    checksum = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        checksum += d
    return len(digits) >= 13 and checksum % 10 == 0


def _phone_or_keep(match: re.Match) -> str:
    """Treat a digit run as a phone number only if it has 10+ digits and is not a date."""
    candidate = match.group(0)
    if DATE_RE.search(candidate) or sum(ch.isdigit() for ch in candidate) < 10:
        return candidate
    return "[phone]"


def redact(text: str) -> str:
    """Replace secrets, card numbers, emails, coordinates and phone numbers with placeholders."""
    text = SECRET_RE.sub("[redacted]", text)
    text = CARD_RE.sub(lambda m: "[card]" if _luhn_ok(m.group(0)) else m.group(0), text)
    text = EMAIL_RE.sub("[email]", text)
    text = COORDS_RE.sub("[coords]", text)
    return PHONE_CANDIDATE_RE.sub(_phone_or_keep, text)


def contains_secret(text: str) -> bool:
    return bool(SECRET_RE.search(text))
