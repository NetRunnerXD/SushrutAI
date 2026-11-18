"""Normalisation of Indian prescription shorthand (Latin abbreviations, 1-0-1 notation, Hindi/Hinglish).

Sources: standard Latin prescription abbreviations (e.g. ISMP list of error-prone abbreviations,
ISMP 2021) and the common Indian "morning-afternoon-night" dot/number notation.
"""
from __future__ import annotations

import re

# label -> (morning, afternoon, night, extra) doses per day pattern
FREQ_ABBR: dict[str, dict] = {
    "od": {"morning": 1, "afternoon": 0, "night": 0, "per_day": 1, "label": "once a day"},
    "qd": {"morning": 1, "afternoon": 0, "night": 0, "per_day": 1, "label": "once a day"},
    "hs": {"morning": 0, "afternoon": 0, "night": 1, "per_day": 1, "label": "at bedtime"},
    "bd": {"morning": 1, "afternoon": 0, "night": 1, "per_day": 2, "label": "twice a day"},
    "bid": {"morning": 1, "afternoon": 0, "night": 1, "per_day": 2, "label": "twice a day"},
    "tds": {"morning": 1, "afternoon": 1, "night": 1, "per_day": 3, "label": "three times a day"},
    "tid": {"morning": 1, "afternoon": 1, "night": 1, "per_day": 3, "label": "three times a day"},
    "qid": {"morning": 1, "afternoon": 1, "night": 1, "per_day": 4, "label": "four times a day"},
    "qds": {"morning": 1, "afternoon": 1, "night": 1, "per_day": 4, "label": "four times a day"},
    "sos": {"morning": 0, "afternoon": 0, "night": 0, "per_day": None, "label": "only when needed"},
    "prn": {"morning": 0, "afternoon": 0, "night": 0, "per_day": None, "label": "only when needed"},
    "stat": {"morning": 0, "afternoon": 0, "night": 0, "per_day": 1, "label": "immediately, once"},
    "weekly": {"morning": 1, "afternoon": 0, "night": 0, "per_day": None, "label": "once a week"},
}
ENGLISH_FREQ = [
    (r"\b(once|one time|1 time)s? (a|per|in a) week\b|\bweekly\b", "weekly"),
    (r"\b(four times|4 times)( a| per)? day\b|\b4 times daily\b", "qid"),
    (r"\b(thrice|three times|3 times)( a| per)? day\b|\b(thrice|three times|3 times) daily\b", "tds"),
    (r"\b(twice|two times|2 times)( a| per)? day\b|\b(twice|two times) daily\b", "bd"),
    (r"\bat (night|bed ?time)\b|\bbefore (sleep|bed)\b", "hs"),
    (r"\b(once|one time)( a| per)? day\b|\bonce daily\b|\b(one|1)( tablet| tab| cap)? daily\b|\bdaily\b", "od"),
    (r"\bif (needed|required)\b|\bwhen (needed|required)\b|\bas needed\b", "sos"),
]
HINDI_FREQ = [
    (r"दिन में (एक|1) बार|ek baar|1 baar", "od"),
    (r"दिन में (दो|2) बार|do baar|2 baar|सुबह[- ]शाम|subah[- ]shaam", "bd"),
    (r"दिन में (तीन|3) बार|teen baar|3 baar", "tds"),
    (r"रात को|सोते समय|raat ko|sote samay", "hs"),
    (r"ज़रूरत (होने )?पर|जरूरत पर|zaroorat par|jarurat par", "sos"),
]

TIMING = [
    (r"\b(a\.?c\.?|before (food|meals?)|empty stomach|khali pet)\b|खाने से पहले|खाली पेट", "before_food"),
    (r"\b(p\.?c\.?|after (food|meals?)|khane ke baad)\b|खाने के बाद", "after_food"),
    (r"\bwith (food|meals?)\b|खाने के साथ", "with_food"),
    (r"\bempty stomach\b|\bfasting\b", "empty_stomach"),
    (r"\bhs\b|bed ?time|सोते समय", "bedtime"),
]

_DOTS = re.compile(r"(?<![\d.])([0-4](?:\.5|½)?|½)\s*[-–—x×]\s*([0-4](?:\.5|½)?|½)\s*[-–—x×]\s*([0-4](?:\.5|½)?|½)(?:\s*[-–—x×]\s*([0-4](?:\.5|½)?|½))?(?![\d.])")
_DURATION = re.compile(
    r"(?:x|×|for|f/|—|-)?\s*(\d{1,3})\s*(d|day|days|दिन|din|w|wk|wks|week|weeks|हफ्ते|सप्ताह|m|mo|month|months|महीने)\b",
    re.I)


def _num(s: str | None) -> float:
    if not s:
        return 0.0
    return float(s.replace("½", "0.5")) if s != "½" else 0.5


def parse_frequency(text: str) -> dict | None:
    t = text.lower()
    m = _DOTS.search(t)
    if m:
        parts = [_num(g) for g in m.groups() if g is not None]
        if len(parts) == 4:  # morning-afternoon-evening-night
            mo, af, ev, ni = parts
            ni += ev
        else:
            mo, af, ni = parts
        per_day = mo + af + ni
        return {"raw": m.group(0), "morning": mo, "afternoon": af, "night": ni, "per_day": per_day,
                "label": f"{mo:g} morning, {af:g} afternoon, {ni:g} night"}
    for pat, code in [*HINDI_FREQ, *ENGLISH_FREQ]:
        if re.search(pat, t):
            return {"raw": code, **FREQ_ABBR[code]}
    for code in sorted(FREQ_ABBR, key=len, reverse=True):
        if re.search(rf"(?<![a-z]){re.escape(code)}(?![a-z])", t.replace(".", "")):
            return {"raw": code, **FREQ_ABBR[code]}
    return None


def parse_timing(text: str) -> str | None:
    t = text.lower()
    for pat, code in TIMING:
        if re.search(pat, t):
            return code
    return None


def parse_duration_days(text: str) -> int | None:
    m = _DURATION.search(text)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    if unit.startswith(("w", "हफ्ते", "सप्ताह")):
        return n * 7
    if unit.startswith(("mo", "month", "महीने")) or unit == "m":
        return n * 30
    return n
