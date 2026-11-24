"""Module 6 & 11: Multi-Problem Clinical Context & Non-Destructive Consultation Memory.

Tracks multiple independent clinical problems/topics within the same consultation
(e.g., current abdominal complaint + newly mentioned diabetes history, or dizziness
+ palpitations + sleep disturbance + weakness during walking) without overwriting
earlier symptoms, suppressing duplicate repetitions, and handling patient corrections.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.clinical.extract import extract_deterministic


@dataclass
class ClinicalTopic:
    topic_id: str
    label: str
    category: str  # current_symptom | chronic_history | medication | lifestyle
    status: str = "active"  # active | resolved | corrected
    severity: str = "unknown"
    duration: str | None = None
    mentions: int = 1
    related_to: list[str] = field(default_factory=list)


@dataclass
class MultiProblemClinicalContext:
    """Maintains all active/historical clinical problems across incremental turns."""

    patient_id: str = "anonymous"
    topics: dict[str, ClinicalTopic] = field(default_factory=dict)
    negated_findings: set[str] = field(default_factory=set)
    corrections: list[dict[str, str]] = field(default_factory=list)
    turn_history: list[str] = field(default_factory=list)

    def ingest_turn(self, text: str, *, lang: str = "en") -> dict[str, Any]:
        """Extract symptoms, chronic conditions, and negations without overwriting prior topics."""
        clean = (text or "").strip()
        if not clean:
            return self.snapshot()
        self.turn_history.append(clean)

        ext = extract_deterministic(clean, patient_id=self.patient_id, language=lang)

        # 1. Active symptoms (suppress duplicates, increment mention count, update severity/duration)
        for sx in ext.symptoms:
            key = sx.name.lower()
            # If previously negated and now reported -> record correction
            if key in self.negated_findings:
                self.negated_findings.discard(key)
                self.corrections.append({"field": key, "change": "negated -> active"})
            if key in self.topics:
                t = self.topics[key]
                t.mentions += 1
                if sx.severity not in ("unknown", "not_assessed"):
                    t.severity = sx.severity
                if sx.duration:
                    t.duration = sx.duration
            else:
                self.topics[key] = ClinicalTopic(
                    topic_id=f"sx:{key}",
                    label=sx.name,
                    category="current_symptom",
                    status="active",
                    severity=sx.severity,
                    duration=sx.duration,
                )

        # 2. Negated symptoms (handle patient changing their answer!)
        for neg in ext.negated_symptoms:
            key = neg.name.lower()
            if key in self.topics and self.topics[key].status == "active":
                self.topics[key].status = "corrected"
                self.corrections.append({"field": key, "change": "active -> denied/corrected"})
            self.negated_findings.add(key)

        # 3. Chronic medical history introduced mid-conversation (e.g. "amar diabetes-o ache")
        for hist in ext.medical_history:
            if hist.category == "chronic_condition" and hist.normalized_name:
                key = f"hx:{hist.normalized_name.lower()}"
                if key not in self.topics:
                    self.topics[key] = ClinicalTopic(
                        topic_id=key,
                        label=hist.description,
                        category="chronic_history",
                        status=hist.status,
                    )

        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        active_syms = [t.label for t in self.topics.values() if t.category == "current_symptom" and t.status == "active"]
        chronic = [t.label for t in self.topics.values() if t.category == "chronic_history"]
        return {
            "active_symptoms": active_syms,
            "chronic_conditions": chronic,
            "negated_findings": sorted(self.negated_findings),
            "topic_count": len(self.topics),
            "corrections": list(self.corrections),
            "topics": [
                {
                    "topic_id": t.topic_id,
                    "label": t.label,
                    "category": t.category,
                    "status": t.status,
                    "severity": t.severity,
                    "duration": t.duration,
                    "mentions": t.mentions,
                }
                for t in self.topics.values()
            ],
        }
