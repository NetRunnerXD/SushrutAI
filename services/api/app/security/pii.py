"""PII scrubbing applied before any text leaves the machine (privacy tiers IN / C).

Regex recognisers for Indian identifiers. Microsoft Presidio can be layered on top for
names/addresses; regexes alone are deliberately conservative (over-redact rather than leak).
"""
from __future__ import annotations

import re

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    # ABHA number: 14 digits, usually 2-4-4-4
    ("ABHA", re.compile(r"(?<!\d)\d{2}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}(?!\d)")),
    # Aadhaar: 12 digits, first digit 2-9, optional spaces/hyphens in 4-4-4 groups
    ("AADHAAR", re.compile(r"(?<!\d)[2-9]\d{3}[\s-]?\d{4}[\s-]?\d{4}(?!\d)")),
    ("ABHA_ADDRESS", re.compile(r"\b[A-Za-z0-9._]{3,}@(abdm|sbx)\b", re.I)),
    ("PAN", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    # Indian mobile: optional +91 / 0, then 10 digits starting 6-9
    ("PHONE", re.compile(r"(?<!\d)(?:\+?91[\s-]?|0)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")),
    ("PINCODE", re.compile(r"(?i)\b(?:pin(?:code)?|पिन)[:\s-]*\d{6}\b")),
    # Doctor registration numbers often printed as "Reg. No. 12345"
    ("REG_NO", re.compile(r"(?i)\breg(?:istration)?\.?\s*no\.?\s*[:\-]?\s*[A-Z0-9/-]{3,}")),
]

# Self-introductions in English / Hindi / Hinglish: "my name is X", "मेरा नाम X है", "mera naam X"
_NAME_INTRO = re.compile(
    r"(?i)(my name is|i am called|मेरा नाम|mera naam|amar naam|আমার নাম|என் பெயர்|నా పేరు)\s+([^\s,.।]+(?:\s+[^\s,.।]+)?)"
)


def scrub(text: str) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}
    out = _NAME_INTRO.sub(lambda m: f"{m.group(1)} [NAME]", text)
    if out != text:
        counts["NAME"] = 1
    for label, pat in _PATTERNS:
        out, n = pat.subn(f"[{label}]", out)
        if n:
            counts[label] = counts.get(label, 0) + n
    return out, counts
