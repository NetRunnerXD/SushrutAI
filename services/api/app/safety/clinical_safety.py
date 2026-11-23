"""Deterministic Clinical Safety & Triage Engine.

Evaluates emergency warning signs independently of LLM probability scores, tracks
positive vs explicitly negative vs unknown vs not_assessed findings, and never claims
a patient is safe merely because no emergency symptom was mentioned.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.clinical.schema import ClinicalExtractionPayload
from app.safety import red_flags

# Core emergency screening checklist
CORE_EMERGENCY_DOMAINS: tuple[tuple[str, str], ...] = (
    ("cardiac_chest_pain", "chest pain or pressure"),
    ("breathing", "difficulty breathing"),
    ("unconscious_seizure", "fainting, unconsciousness, or seizures"),
    ("stroke_fast", "face droop, slurred speech, or one-sided weakness"),
    ("severe_bleeding", "severe bleeding or vomiting blood"),
    ("severe_allergy_anaphylaxis", "severe allergic swelling of face/lips/throat"),
    ("severe_confusion", "sudden severe confusion"),
)


@dataclass
class SafetyAssessment:
    escalate_now: bool
    severity: Literal["emergency", "mental_health", "urgent", "routine_uncertain"]
    interrupt_questioning: bool
    rule_ids: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    call_numbers: list[str] = field(default_factory=list)
    positive_findings: list[str] = field(default_factory=list)
    negative_findings: list[str] = field(default_factory=list)
    unknown_findings: list[str] = field(default_factory=list)
    not_assessed: list[str] = field(default_factory=list)
    safety_note: str = "Absence of reported warning signs does not rule out serious illness."

    def as_dict(self) -> dict:
        return {
            "escalate_now": self.escalate_now,
            "severity": self.severity,
            "interrupt_questioning": self.interrupt_questioning,
            "rule_ids": self.rule_ids,
            "messages": self.messages,
            "call_numbers": self.call_numbers,
            "positive_findings": self.positive_findings,
            "negative_findings": self.negative_findings,
            "unknown_findings": self.unknown_findings,
            "not_assessed": self.not_assessed,
            "safety_note": self.safety_note,
        }


def evaluate_safety(
    text: str,
    *,
    lang: str = "en",
    age_years: float | None = None,
    extraction: ClinicalExtractionPayload | None = None,
    answered_negatives: list[str] | None = None,
) -> SafetyAssessment:
    """Run deterministic safety evaluation across text, extracted clinical concepts, and vitals."""
    rf = red_flags.check(text, lang=lang, age_years=age_years)
    rules = list(rf.rules)
    messages = list(rf.messages)
    numbers = list(rf.call_numbers)
    sev: Literal["emergency", "mental_health", "urgent", "routine_uncertain"] = (
        rf.kind if rf.kind in ("emergency", "mental_health", "urgent") else "routine_uncertain"
    )
    positives: list[str] = []
    negatives: list[str] = list(answered_negatives or [])
    unknowns: list[str] = []

    if extraction is not None:
        for sx in extraction.symptoms:
            positives.append(sx.name)
            if sx.name in ("chest pain", "shortness of breath", "fainting"):
                tag = {
                    "chest pain": "cardiac_chest_pain",
                    "shortness of breath": "breathing",
                    "fainting": "unconscious_seizure",
                }[sx.name]
                if tag not in rules:
                    rules.append(tag)
                    sev = "emergency"
                    if not numbers:
                        numbers = ["108", "112"]
                    if not messages:
                        fallback_msg = red_flags.LEAD.get(lang, {}).get("emergency") or (
                            "Possible emergency warning sign detected. Please call 108 / 112 or seek emergency care immediately."
                        )
                        messages.append(fallback_msg)
            if sx.severity == "unknown":
                unknowns.append(f"{sx.name} severity")
            if sx.temporal.is_temporal_uncertain:
                unknowns.append(f"{sx.name} onset/duration")

        for neg in extraction.negated_symptoms:
            negatives.append(neg.name)

        # Critical vitals check (measured or estimated)
        for vm in extraction.vital_measurements:
            if vm.vital_type == "spo2" and vm.value < 90:
                rules.append("critical_hypoxia")
                sev = "emergency"
                numbers = ["108", "112"]
                messages.append(
                    "এটি জরুরি অবস্থা হতে পারে — অক্সিজেন খুব কম। এখনই 108 বা 112 নম্বরে ফোন করুন।"
                    if lang == "bn"
                    else "Very low oxygen reading (<90%). Call 108 / 112 or go to emergency care immediately."
                )
            elif vm.vital_type == "temp" and vm.value >= 40.0:
                rules.append("hyperpyrexia")
                if sev != "emergency":
                    sev = "urgent"
                numbers = numbers or ["108", "112"]
                messages.append(
                    "খুব বেশি জ্বর (≥40°C / 104°F) — আজই জরুরি ডাক্তার দেখান।"
                    if lang == "bn"
                    else "Very high fever (≥40°C / 104°F) needs urgent medical evaluation today."
                )
            elif vm.vital_type == "bp_sys" and vm.value >= 180:
                rules.append("hypertensive_crisis")
                sev = "emergency"
                numbers = ["108", "112"]
                messages.append(
                    "রক্তচাপ অত্যন্ত বেশি (≥180 mmHg) — এখনই জরুরি চিকিৎসা নিন বা 108/112-এ কল করুন।"
                    if lang == "bn"
                    else "Dangerously high blood pressure (≥180 systolic). Seek emergency care immediately or call 108 / 112."
                )

    neg_set = {n.lower() for n in negatives}
    not_assessed: list[str] = []
    for r_id, desc in CORE_EMERGENCY_DOMAINS:
        if r_id in rules:
            continue
        if any(tok in neg_set for tok in desc.lower().split()):
            continue
        not_assessed.append(desc)

    escalate = sev in ("emergency", "mental_health")
    return SafetyAssessment(
        escalate_now=escalate,
        severity=sev,
        interrupt_questioning=escalate,
        rule_ids=list(dict.fromkeys(rules)),
        messages=list(dict.fromkeys(messages)),
        call_numbers=list(dict.fromkeys(numbers)),
        positive_findings=list(dict.fromkeys(positives)),
        negative_findings=list(dict.fromkeys(negatives)),
        unknown_findings=list(dict.fromkeys(unknowns)),
        not_assessed=not_assessed,
    )
