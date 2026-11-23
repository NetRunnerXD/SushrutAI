"""Pull health values out of what someone says, so they are remembered without a form.

Two passes:
- `quick_facts()`: instant and deterministic (readings, fever with a number, how long, when it started). Shown to
  the person straight away as "Saving: 101 °F · 2 days" chips they can undo.
- `model_facts()`: a small model reads the same turn in the background for what rules cannot list (which
  symptoms are present or absent, how severe, what seems to trigger them, how a medicine worked). Nothing here
  is a fixed list of diseases or symptoms: names come from the person's own words.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta

from app.assistant.actions import parse_vitals
from app.clock import local_now

VITAL_UNITS = {"bp_sys": "mmHg", "bp_dia": "mmHg", "glucose_f": "mg/dL", "glucose_pp": "mg/dL", "glucose_r": "mg/dL",
               "weight": "kg", "hr": "bpm", "spo2": "%", "temp": "°C"}
VITAL_NAMES = {"bp_sys": "bp systolic", "bp_dia": "bp diastolic", "glucose_f": "fasting sugar",
               "glucose_pp": "after-meal sugar", "glucose_r": "sugar", "weight": "weight", "hr": "pulse",
               "spo2": "oxygen", "temp": "temperature"}

_NUM_WORDS = {"one": 1, "a": 1, "an": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "ten": 10,
              "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "saat": 7,
              "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5, "सात": 7}
_UNIT = {"hour": "hours", "hours": "hours", "hr": "hours", "hrs": "hours", "ghante": "hours", "ghanta": "hours", "घंटे": "hours",
         "day": "days", "days": "days", "din": "days", "दिन": "days", "dino": "days", "दिनों": "days",
         "week": "weeks", "weeks": "weeks", "hafte": "weeks", "hafta": "weeks", "हफ्ते": "weeks", "हफ़्ते": "weeks",
         "month": "months", "months": "months", "mahine": "months", "महीने": "months"}
_N = r"(\d{1,3}|" + "|".join(sorted(map(re.escape, _NUM_WORDS), key=len, reverse=True)) + r")"
_U = "(" + "|".join(sorted(map(re.escape, _UNIT), key=len, reverse=True)) + ")"
_DURATION = re.compile(rf"(?i)(?:for|since|past|last|from)?\s*{_N}\s*{_U}\b(?:\s*(?:se|से|now|ago))?")
_FEVER_NUM = re.compile(r"(?i)(?:fever|temperature|temp|bukhar|bukhaar|बुखार|ताप)\D{0,12}(\d{2,3}(?:\.\d)?)\s*(?:°|degree|deg)?\s*([fc])?\b")


@dataclass
class Fact:
    kind: str
    name: str
    value: dict
    observed_at: object = None
    confidence: float = 1.0
    extra: dict = field(default_factory=dict)

    def label(self) -> str:
        v = self.value
        if self.kind == "vital":
            return f"{self.name} {v.get('value')}{(' ' + v['unit']) if v.get('unit') else ''}"
        if self.kind == "duration":
            return f"{v.get('amount')} {v.get('unit')}"
        if self.kind == "symptom":
            return self.name if v.get("present", True) else f"no {self.name}"
        return self.name


def quick_facts(text: str) -> list[Fact]:
    """Readings, a fever value and how long it has been going on: instant, no model."""
    out: list[Fact] = []
    seen: set[str] = set()
    for r in parse_vitals(text):
        out.append(Fact("vital", VITAL_NAMES.get(r["type"], r["type"]),
                        {"type": r["type"], "value": r["value"], "unit": VITAL_UNITS.get(r["type"], "")}))
        seen.add(r["type"])
    if "temp" not in seen and (m := _FEVER_NUM.search(text)):
        v = float(m.group(1))
        unit = (m.group(2) or "").lower()
        c = round((v - 32) * 5 / 9, 1) if (unit == "f" or (not unit and 93 <= v <= 110)) else v
        if 34 <= c <= 44:
            said_f = v if c != v else None  # what the thermometer showed, in °F (shown back exactly as said)
            out.append(Fact("vital", "temperature", {"type": "temp", "value": c, "unit": "°C", "said": m.group(0).strip(),
                                                     **({"f": v} if said_f else {})}))
    if m := _DURATION.search(text):
        raw, unit = m.group(1).lower(), _UNIT[m.group(2).lower()]
        amount = int(raw) if raw.isdigit() else _NUM_WORDS.get(raw, 0)
        if 0 < amount <= 365:
            days = {"hours": amount / 24, "days": amount, "weeks": amount * 7, "months": amount * 30}[unit]
            onset = local_now() - timedelta(days=days)
            out.append(Fact("duration", "duration", {"amount": amount, "unit": unit, "days": round(days, 2),
                                                      "onset": onset.date().isoformat()}))
    return out


# ---------------------------------------------------------------- the model pass (background)
FACTS_SCHEMA = {
    "type": "object",
    "properties": {
        "symptoms": {"type": "array", "maxItems": 8, "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "present": {"type": "boolean"},
            "severity": {"type": "string", "enum": ["mild", "moderate", "severe", "unknown"]},
            "trigger": {"type": "string"}}, "required": ["name", "present"]}},
        "medicine_response": {"type": "array", "maxItems": 4, "items": {"type": "object", "properties": {
            "medicine": {"type": "string"}, "effect": {"type": "string", "enum": ["better", "same", "worse", "side_effect"]},
            "note": {"type": "string"}}, "required": ["medicine", "effect"]}},
        "context": {"type": "array", "maxItems": 4, "items": {"type": "string"}},
    },
    "required": ["symptoms"],
}
_FACTS_PROMPT = (
    "Read one message a patient wrote about their health. List only what the message itself says, in English, "
    "lower case, short names (\"fever\", \"loose motion\", \"body ache\"):\n"
    "- symptoms: each symptom mentioned; present=false if they say they do NOT have it; severity if stated; "
    "trigger if they link it to something (\"after eating outside\").\n"
    "- medicine_response: if they say a medicine helped, did nothing, made things worse or caused a side effect.\n"
    "- context: events they mention as anchors (\"after the wedding\", \"since the trip\").\n"
    "Do not guess diagnoses. Empty lists when nothing applies. Respond only with JSON.")


_EMPTY = {"", "unknown", "none", "nothing", "n/a", "na", "not stated", "not mentioned", "no trigger", "null"}
_STOP = {"after", "before", "since", "from", "with", "when", "during", "that", "this", "the", "and", "while", "then",
         "eating", "having", "being", "some", "last", "time"}


def _content_words(s: str) -> list[str]:
    return [w for w in re.findall(r"\w+", s.lower()) if len(w) > 3 and w not in _STOP]


async def model_facts(text_en: str) -> list[Fact]:
    """Symptoms (present / absent), severity, triggers, medicine responses and context anchors from the
    small model. Empty list when the model is not available (the quick facts still stand)."""
    from app.config import get_settings
    from app.llm.providers import ProviderUnavailable, ollama

    s = get_settings()
    model = s.instant_llm_model or s.fast_llm_model or s.llm_model
    try:
        res = await ollama().chat(model, [{"role": "system", "content": _FACTS_PROMPT},
                                          {"role": "user", "content": text_en[:800]}],
                                  schema=FACTS_SCHEMA, temperature=0.0, num_ctx=2048, num_predict=300)
        data = res.json()
    except (ProviderUnavailable, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    out: list[Fact] = []
    low = text_en.lower()
    for sx in data.get("symptoms") or []:
        name = re.sub(r"\s+", " ", str(sx.get("name", "")).strip().lower())[:60]
        if not name or len(name) < 3:
            continue
        # keep only symptoms the message actually talks about (a word of the name appears in it)
        if not any(w in low for w in name.split() if len(w) > 2):
            continue
        trig = str(sx.get("trigger") or "").strip().lower()
        if trig in _EMPTY or not any(w in low for w in _content_words(trig)):
            trig = ""  # "unknown", "nothing", or a trigger the message never mentions
        out.append(Fact("symptom", name, {"present": bool(sx.get("present", True)),
                                          "severity": sx.get("severity") or "unknown",
                                          **({"trigger": trig[:80]} if trig else {})}, confidence=0.7))
    for mr in data.get("medicine_response") or []:
        med = str(mr.get("medicine", "")).strip().lower()[:60]
        if med and med.split()[0] in low:
            out.append(Fact("medicine_response", med, {"effect": mr.get("effect"), "note": (mr.get("note") or "")[:120]},
                            confidence=0.7))
    for c in (data.get("context") or [])[:4]:
        c = str(c).strip().lower()[:60]
        words = _content_words(c)
        # every meaningful word of the anchor must be in the message ("since the trip" needs "trip", not "since")
        if c and words and all(w in low for w in words):
            out.append(Fact("context", c, {"anchor": c}, confidence=0.6))
    return out
