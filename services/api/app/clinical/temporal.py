"""Dedicated multilingual temporal & progression extraction engine.

Handles English, Bengali (native script + Banglish), and Hindi/Hinglish temporal expressions.
Rules:
- Never confuse reporting timestamp (`reference_dt`) with symptom onset.
- Convert relative dates ('yesterday morning', 'two days ago') ONLY when anchor is clear.
- Retain explicit uncertainty ('a few days ago', 'many years') without inventing an exact date.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from app.clock import local_now
from app.clinical.schema import ProgressionState, TemporalInfo

_BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_HI_DIGITS = str.maketrans("०१२३४৫६७८९", "0123456789")

_NUM_WORDS: dict[str, float] = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30,
    "half": 0.5,
    # Bengali words & Banglish
    "এক": 1, "একটা": 1, "দুই": 2, "দু": 2, "তিন": 3, "চার": 4, "পাঁচ": 5, "পাঁচটা": 5, "ছয়": 6, "ছ": 6,
    "সাত": 7, "আট": 8, "নয়": 9, "দশ": 10, "এক সপ্তাহ": 7, "এক মাস": 30,
    "ek": 1, "ekta": 1, "du": 2, "dui": 2, "do": 2, "teen": 3, "tin": 3, "char": 4, "chaar": 4,
    "paanch": 5, "panch": 5, "choy": 6, "saat": 7, "sat": 7, "aat": 8, "aath": 8, "noy": 9, "das": 10,
    # Hindi Devanagari
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5, "छह": 6, "सात": 7, "आठ": 8, "दस": 10,
}

_UNIT_MAP: dict[str, str] = {
    "minute": "minutes", "minutes": "minutes", "min": "minutes", "mins": "minutes", "মিনিট": "minutes", "मिनट": "minutes",
    "hour": "hours", "hours": "hours", "hr": "hours", "hrs": "hours", "ঘণ্টা": "hours", "ঘন্টা": "hours", "ghanta": "hours", "ghante": "hours", "घंटा": "hours", "घंटे": "hours",
    "day": "days", "days": "days", "দিন": "days", "din": "days", "dino": "days", "दिन": "days", "दिनों": "days",
    "week": "weeks", "weeks": "weeks", "wk": "weeks", "wks": "weeks", "সপ্তাহ": "weeks", "সপ্তা": "weeks", "shopta": "weeks", "hafte": "weeks", "hafta": "weeks", "हफ्ते": "weeks", "सप्ताह": "weeks",
    "month": "months", "months": "months", "mo": "months", "মাস": "months", "maash": "months", "mahine": "months", "mahina": "months", "महीने": "months", "महीना": "months",
    "year": "years", "years": "years", "yr": "years", "yrs": "years", "বছর": "years", "bochor": "years", "saal": "years", "সাল": "years", "साल": "years",
}

_N_PAT = r"(\d{1,3}(?:\.\d)?|" + "|".join(sorted(map(re.escape, _NUM_WORDS), key=len, reverse=True)) + r")"
_U_PAT = r"(" + "|".join(sorted(map(re.escape, _UNIT_MAP), key=len, reverse=True)) + r")"

_DURATION_EXACT = re.compile(
    rf"(?i)(?:for|since|past|last|over|from)?\s*{_N_PAT}\s*{_U_PAT}(?=$|[\s,.!?।;:])"
    rf"(?:\s*(?:ago|back|now|dhore|ধরে|theke|থেকে|se|से|আগে|पहले))?",
    re.UNICODE,
)

_VAGUE_DURATION = re.compile(
    r"(?i)(?:for\s+)?(?:a\s+few|several|some|couple\s+of|many|lots\s+of)\s+(hours?|days?|weeks?|months?|years?)|"
    r"(?:কিছু|কয়েক|অনেক|বহু)\s*(?:ঘণ্টা|ঘন্টা|দিন|সপ্তাহ|মাস|বছর)(?:\s*(?:ধরে|আগে|থেকে))?|"
    r"(?:koyek|kichu|onek|bohu)\s*(?:din|shopta|maash|bochor)(?:\s*(?:dhore|age|theke))?|"
    r"(?:kuch|kai|bahut)\s*(?:dino|hafte|mahino|saalo)(?:\s*(?:se|pehle))?",
    re.UNICODE,
)

# Relative onset anchors
_YESTERDAY_MORNING = re.compile(r"(?i)\b(?:yesterday\s+morning|kal\s+subah)\b|গতকাল\s+সকাল|कल\s+सुबह")
_YESTERDAY_NIGHT = re.compile(r"(?i)\b(?:last\s+night|yesterday\s+night|kal\s+raat)\b|গত\s*রাত|গতকাল\s+রাত|कल\s+रात")
_YESTERDAY = re.compile(r"(?i)\b(?:yesterday|gotokal|kal)\b|গতকাল|कल")
_THIS_MORNING = re.compile(r"(?i)\b(?:this\s+morning|today\s+morning|aj\s+shokal|aaj\s+subah)\b|আজ\s+সকাল|आज\s+सुबह")
_TODAY = re.compile(r"(?i)\b(?:since\s+today|started\s+today|aaj\s+se|aj\s+theke)\b|আজ\s+থেকে|आज\s+से")

# Frequency / timing pattern
_FREQUENCY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)comes?\s+and\s+goes?|on\s+and\s+off|sometimes|occasionally|intermittent|মাঝে\s*মাঝে|কখনো\s*কখনো|majhe\s+majhe|kabhi\s+kabhi|कभी\s*कभी"), "intermittent"),
    (re.compile(r"(?i)all\s+the\s+time|continuous(?:ly)?|constant(?:ly)?|non[- ]?stop|সবসময়|সারাক্ষণ|একটানা|lagatar|लगातार"), "continuous"),
    (re.compile(r"(?i)after\s+(?:eating|food|meals?)|post\s*prandial|খাওয়ার\s+পর|খাবার\s+পর|khane\s+ke\s+baad|खाने\s+के\s+बाद"), "after_eating"),
    (re.compile(r"(?i)(?:when|while|on)\s+(?:i\s+)?stand(?:ing)?\s+up|উঠে\s+দাঁড়ালে|দাঁড়ালে|darale|uthle|khade\s+hone\s+par|खड़े\s+होने\s+पर"), "on_standing"),
    (re.compile(r"(?i)at\s+night|every\s+night|nocturnal|রাতে|রাতের\s+দিকে|raate|raat\s+ko|रात\s+को"), "nocturnal"),
    (re.compile(r"(?i)every\s+morning|in\s+the\s+morning|সকালে|সকালের\s+দিকে|shokale|subah|सुबह"), "morning"),
    (re.compile(r"(?i)every\s+other\s+day|alternate\s+day|একদিন\s+পর\s+পর|ekdin\s+por|din\s+chhod"), "alternate_days"),
)

# Progression cues
_WORSENING = re.compile(
    r"(?i)(?:got|get|getting|become|became|becoming|growing)\s+worse|worsen(?:ed|ing)?|more\s+severe|increasing|deteriorat|"
    r"বেড়ে\s+গেছে|বাড়ছে|আরও\s+খারাপ|খারাপ\s+হয়েছে|bere\s+geche|badh\s+raha|बढ़\s+गया|और\s+खराब|zyada\s+ho\s+gaya"
)
_IMPROVING = re.compile(
    r"(?i)(?:feel|feeling|got|getting)\s+better|improv(?:ed|ing)|less\s+severe|subsided|decreased|"
    r"আগের\s+চেয়ে\s+ভালো|কমছে|কমেছে|ভালো\s+লাগছে|bhalo\s+lagche|komche|behtar|कम\s+हुआ|ठीक\s+हो\s+रहा"
)
_RESOLVED = re.compile(
    r"(?i)(?:completely\s+)?(?:gone|resolved|cured|stopped|no\s+longer\s+have|cleared\s+up)|"
    r"পুরো\s+সেরে\s+গেছে|চলে\s+গেছে|এখন\s+আর\s+নেই|sere\s+geche|chole\s+geche|theek\s+ho\s+gaya|ठीक\s+हो\s+गया"
)
_STABLE = re.compile(r"(?i)\b(?:same\s+as\s+before|unchanged|staying\s+the\s+same)\b|একই\s+রকম|আগের\s+মতোই|vaisa\s+hi")


def _norm_digits(text: str) -> str:
    return (text or "").translate(_BN_DIGITS).translate(_HI_DIGITS)


def _to_days(amount: float, unit: str) -> float:
    return {
        "minutes": amount / (24 * 60),
        "hours": amount / 24.0,
        "days": amount,
        "weeks": amount * 7.0,
        "months": amount * 30.0,
        "years": amount * 365.0,
    }[unit]


def extract_progression(text: str) -> ProgressionState:
    t = text or ""
    if _RESOLVED.search(t):
        return "resolved"
    if _WORSENING.search(t):
        return "worsening"
    if _IMPROVING.search(t):
        return "improving"
    if _STABLE.search(t):
        return "stable"
    return "unknown"


def extract_frequency(text: str) -> str | None:
    t = text or ""
    hits = [label for pat, label in _FREQUENCY_PATTERNS if pat.search(t)]
    return ", ".join(hits) if hits else None


def extract_temporal(text: str, reference_dt: datetime | None = None) -> TemporalInfo:
    """Extract structured temporal attributes from a patient statement.

    `reference_dt`: observation / reporting timestamp (defaults to local_now() when omitted,
    never confused with onset).
    """
    ref = reference_dt or local_now().replace(tzinfo=None)
    t = _norm_digits(text)
    obs_iso = ref.replace(microsecond=0).isoformat()

    progression = extract_progression(t)
    frequency = extract_frequency(t)

    # 1. Exact relative anchors (yesterday morning, last night, X days ago, etc.)
    if _YESTERDAY_MORNING.search(t):
        onset_dt = (ref - timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
        days = round(max(0.0, (ref - onset_dt).total_seconds() / 86400), 2)
        return TemporalInfo(
            reported_onset="yesterday morning",
            estimated_onset=onset_dt.date().isoformat(),
            exact_observation_time=obs_iso,
            duration_text=f"since yesterday morning (~{days} days)",
            duration_days=days,
            frequency=frequency,
            progression=progression,
            last_occurrence=obs_iso if progression != "resolved" else None,
            is_approximate=True,
            is_temporal_uncertain=False,
        )

    if _YESTERDAY_NIGHT.search(t):
        onset_dt = (ref - timedelta(days=1)).replace(hour=21, minute=0, second=0, microsecond=0)
        days = round(max(0.0, (ref - onset_dt).total_seconds() / 86400), 2)
        return TemporalInfo(
            reported_onset="last night",
            estimated_onset=onset_dt.date().isoformat(),
            exact_observation_time=obs_iso,
            duration_text="since last night",
            duration_days=days,
            frequency=frequency,
            progression=progression,
            last_occurrence=obs_iso if progression != "resolved" else None,
            is_approximate=True,
            is_temporal_uncertain=False,
        )

    if _THIS_MORNING.search(t):
        onset_dt = ref.replace(hour=8, minute=0, second=0, microsecond=0)
        return TemporalInfo(
            reported_onset="this morning",
            estimated_onset=onset_dt.date().isoformat(),
            exact_observation_time=obs_iso,
            duration_text="since this morning",
            duration_days=round(max(0.0, (ref - onset_dt).total_seconds() / 86400), 2),
            frequency=frequency,
            progression=progression,
            last_occurrence=obs_iso if progression != "resolved" else None,
            is_approximate=True,
            is_temporal_uncertain=False,
        )

    # 2. Explicit numeric/word duration ("for five days", "two days ago", "পাঁচ দিন ধরে")
    if m := _DURATION_EXACT.search(t):
        raw_num = m.group(1).lower()
        unit_raw = m.group(2).lower()
        unit = _UNIT_MAP.get(unit_raw, "days")
        amount = float(raw_num) if re.match(r"^\d+(?:\.\d+)?$", raw_num) else _NUM_WORDS.get(raw_num, 0.0)
        if 0 < amount <= 3650:
            days = round(_to_days(amount, unit), 2)
            # For years >= 2 ("for 5 years"), retain approximate uncertainty rather than inventing exact calendar day
            too_broad = unit == "years" and amount >= 1
            est_onset = None if too_broad else (ref - timedelta(days=days)).date().isoformat()
            phrase = m.group(0).strip()
            # If the phrase explicitly represents a prior improved/resolved reference ("feeling better two days ago")
            last_occ = est_onset if ("ago" in phrase.lower() and progression == "improving") else (
                obs_iso if progression != "resolved" else None
            )
            return TemporalInfo(
                reported_onset=phrase,
                estimated_onset=est_onset,
                exact_observation_time=obs_iso,
                duration_text=f"{int(amount) if amount.is_integer() else amount} {unit}",
                duration_days=days,
                frequency=frequency,
                progression=progression,
                last_occurrence=last_occ,
                is_approximate=too_broad or unit in ("weeks", "months"),
                is_temporal_uncertain=too_broad,
            )

    if _YESTERDAY.search(t):
        onset_dt = ref - timedelta(days=1)
        return TemporalInfo(
            reported_onset="yesterday",
            estimated_onset=onset_dt.date().isoformat(),
            exact_observation_time=obs_iso,
            duration_text="1 day",
            duration_days=1.0,
            frequency=frequency,
            progression=progression,
            last_occurrence=obs_iso if progression != "resolved" else None,
            is_approximate=True,
            is_temporal_uncertain=False,
        )

    if _TODAY.search(t):
        return TemporalInfo(
            reported_onset="today",
            estimated_onset=ref.date().isoformat(),
            exact_observation_time=obs_iso,
            duration_text="today",
            duration_days=0.25,
            frequency=frequency,
            progression=progression,
            last_occurrence=obs_iso if progression != "resolved" else None,
            is_approximate=True,
            is_temporal_uncertain=False,
        )

    # 3. Vague / uncertain durations ("a few days", "many years", "কিছু দিন ধরে") -> NEVER invent exact onset date!
    if vm := _VAGUE_DURATION.search(t):
        return TemporalInfo(
            reported_onset=vm.group(0).strip(),
            estimated_onset=None,
            exact_observation_time=obs_iso,
            duration_text=vm.group(0).strip(),
            duration_days=None,
            frequency=frequency,
            progression=progression,
            last_occurrence=obs_iso if progression != "resolved" else None,
            is_approximate=True,
            is_temporal_uncertain=True,
        )

    return TemporalInfo(
        reported_onset=None,
        estimated_onset=None,
        exact_observation_time=obs_iso,
        duration_text=None,
        duration_days=None,
        frequency=frequency,
        progression=progression,
        last_occurrence=obs_iso if progression != "resolved" else None,
        is_approximate=False,
        is_temporal_uncertain=True,
    )
