"""Patient-dependent Clinical Investigation Recommendation Engine.

Selects investigations tailored to the patient's symptoms, duration, medications, and safety findings
(NEVER a universal test package), checks existing LabResults already in the patient's chart, and
clearly states purpose (screening | diagnostic | monitoring), urgency, limitations, and clinician review.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.memory.context import MemoryContext
from app.models import ClinicalInvestigation, LabResult
from app.safety.clinical_safety import SafetyAssessment
from app.security.auth import CurrentUser


@dataclass(frozen=True)
class InvestigationTemplate:
    name: str
    aliases: tuple[str, ...]
    purpose_category: str  # screening | diagnostic | monitoring
    why_relevant: str
    trigger_cues: tuple[str, ...]
    min_duration_days: float = 0.0
    urgent_cues: tuple[str, ...] = ()
    limitations: str = "A single test result must be interpreted alongside physical examination by a clinician."


_CATALOGUE: tuple[InvestigationTemplate, ...] = (
    InvestigationTemplate(
        name="Complete Blood Count (CBC)",
        aliases=("cbc", "haemoglobin", "hemoglobin", "hb", "platelets", "wbc"),
        purpose_category="diagnostic",
        why_relevant="Checks for anaemia (low haemoglobin causing weakness/dizziness), infection, or low platelets in prolonged fever.",
        trigger_cues=("weakness", "dizziness", "fever", "head heaviness", "malaise", "pale", "fatigue", "দুর্বল", "জ্বর"),
        min_duration_days=2.0,
        urgent_cues=("bleeding", "petechiae", "fainting"),
    ),
    InvestigationTemplate(
        name="Blood glucose (fasting / random)",
        aliases=("glucose", "blood sugar", "rbs", "fbs", "ppbs", "sugar"),
        purpose_category="screening",
        why_relevant="Evaluates low or high blood sugar as a contributor to dizziness, weakness, sweating, or fatigue.",
        trigger_cues=("dizziness", "weakness", "diabetes", "metformin", "glimepiride", "insulin", "tremor", "sweating", "head heaviness"),
    ),
    InvestigationTemplate(
        name="HbA1c",
        aliases=("hba1c", "a1c", "glycated"),
        purpose_category="monitoring",
        why_relevant="Measures 3-month average blood sugar control for existing diabetes or persistent metabolic risk.",
        trigger_cues=("diabetes", "metformin", "glimepiride", "insulin", "high sugar"),
    ),
    InvestigationTemplate(
        name="Serum Electrolytes (Sodium, Potassium)",
        aliases=("electrolytes", "sodium", "potassium", "na+", "k+"),
        purpose_category="diagnostic",
        why_relevant="Checks salt imbalances from poor oral intake, vomiting, diarrhea, heat exposure, or BP medicines.",
        trigger_cues=("vomiting", "diarrhea", "poor oral intake", "dehydration", "bp", "amlodipine", "telmisartan", "furosemide", "severe weakness"),
    ),
    InvestigationTemplate(
        name="Kidney Function Tests (Creatinine / BUN)",
        aliases=("creatinine", "bun", "kft", "rft", "kidney"),
        purpose_category="monitoring",
        why_relevant="Checks kidney filtration before/during chronic medications, dehydration, or high blood pressure.",
        trigger_cues=("kidney", "bp", "hypertension", "swelling", "urine", "diabetes", "telmisartan"),
    ),
    InvestigationTemplate(
        name="Liver Function Tests (LFT)",
        aliases=("lft", "sgpt", "sgot", "alt", "ast", "bilirubin", "liver"),
        purpose_category="diagnostic",
        why_relevant="Helpful if there is nausea, yellowing of eyes (jaundice), upper abdominal pain, or prolonged fever.",
        trigger_cues=("jaundice", "yellow", "right upper abdomen", "nausea", "vomiting", "hepatitis", "alcohol"),
    ),
    InvestigationTemplate(
        name="Thyroid Function (TSH)",
        aliases=("tsh", "thyroid", "t3", "t4"),
        purpose_category="screening",
        why_relevant="Assesses thyroid imbalance when unexplained chronic fatigue, palpitations, or weight changes are present.",
        trigger_cues=("thyroid", "palpitation", "fatigue", "weeks", "weight"),
        min_duration_days=14.0,
    ),
    InvestigationTemplate(
        name="Iron Studies / Ferritin",
        aliases=("ferritin", "iron", "tibc"),
        purpose_category="diagnostic",
        why_relevant="Identifies iron depletion when weakness/dizziness accompanies anaemia or heavy blood loss.",
        trigger_cues=("anaemia", "anemia", "low haemoglobin", "heavy periods", "persistent weakness"),
        min_duration_days=5.0,
    ),
    InvestigationTemplate(
        name="Vitamin B12",
        aliases=("b12", "vitamin b12", "cobalamin"),
        purpose_category="diagnostic",
        why_relevant="Checks B12 deficiency when weakness, numbness/tingling, or vegetarian diet history is present.",
        trigger_cues=("tingling", "numbness", "veg", "vegan", "metformin", "persistent fatigue"),
        min_duration_days=7.0,
    ),
    InvestigationTemplate(
        name="Urinalysis",
        aliases=("urine", "urinalysis", "pus cells"),
        purpose_category="diagnostic",
        why_relevant="Evaluates urinary infection or dehydration when fever, burning urination, or flank pain occurs.",
        trigger_cues=("burning urination", "dysuria", "urinary", "flank pain", "fever"),
    ),
    InvestigationTemplate(
        name="12-lead ECG",
        aliases=("ecg", "ekg", "electrocardiogram"),
        purpose_category="diagnostic",
        why_relevant="Evaluates heart rhythm and ischemia urgently when fainting, chest symptoms, palpitations, or severe unexplained dizziness occur.",
        trigger_cues=("chest pain", "palpitation", "fainting", "shortness of breath", "blackout", "orthostatic", "standing up"),
        urgent_cues=("chest pain", "fainting", "shortness of breath", "palpitation"),
        limitations="A single resting ECG only captures rhythm at that moment; intermittent arrhythmias may need longer monitoring.",
    ),
    InvestigationTemplate(
        name="Holter Monitoring",
        aliases=("holter", "ambulatory ecg"),
        purpose_category="monitoring",
        why_relevant="Tracks intermittent heart rhythm changes if episodic palpitations or unexplained near-fainting recur despite normal resting ECG.",
        trigger_cues=("intermittent palpitation", "recurrent fainting", "episodic blackout"),
        min_duration_days=3.0,
    ),
    InvestigationTemplate(
        name="Echocardiography",
        aliases=("echo", "echocardiogram", "2d echo"),
        purpose_category="diagnostic",
        why_relevant="Ordered by a physician when structural heart disease or exertional syncope/breathlessness is suspected.",
        trigger_cues=("heart failure", "exertional breathlessness", "swollen ankles", "cardiac syncope"),
    ),
)


class InvestigationRecommendationEngine:
    """Produces patient-dependent investigation considerations rather than generic bundles."""

    async def recommend(
        self,
        db: AsyncSession,
        cu: CurrentUser,
        *,
        encounter_id: str,
        mem: MemoryContext,
        safety: SafetyAssessment,
        user_text: str = "",
        max_items: int = 5,
    ) -> list[dict]:
        syms = mem.short_term.get("active_symptoms", [])
        max_days = 0.0
        for s in syms:
            dtxt = str(s.get("duration") or "")
            if m := re.search(r"(\d+(?:\.\d+)?)\s*(day|week|month)", dtxt, re.I):
                val = float(m.group(1))
                mult = 7 if m.group(2).lower().startswith("w") else 30 if m.group(2).lower().startswith("m") else 1
                max_days = max(max_days, val * mult)

        signal = " ".join([
            user_text or "",
            " ".join(s.get("name", "") for s in syms),
            " ".join(t for s in syms for t in (s.get("triggers") or [])),
            " ".join(s.get("functional_impact") or "" for s in syms),
            " ".join(m.get("name", "") for m in mem.long_term.get("medications", []) if m.get("name")),
            " ".join(c.get("name", "") for c in mem.long_term.get("conditions", []) if c.get("name")),
            " ".join(safety.positive_findings),
            mem.long_term.get("demographics", {}).get("diet") or "",
        ]).lower()

        if not signal.strip():
            return []

        existing_labs = list(await db.scalars(
            select(LabResult).where(LabResult.user_id == cu.id).order_by(LabResult.observed_at.desc()).limit(30)
        ))

        candidates: list[tuple[int, InvestigationTemplate, list[str], LabResult | None, str]] = []
        for tpl in _CATALOGUE:
            if max_days < tpl.min_duration_days and tpl.min_duration_days > 0:
                continue
            matched = [cue for cue in tpl.trigger_cues if cue in signal]
            if not matched:
                continue
            urg = "urgent" if (safety.escalate_now or any(u in signal for u in tpl.urgent_cues)) else "routine"
            # Check if patient already has matching lab in records
            matched_lab = None
            for lr in existing_labs:
                tname = lr.test_name.lower()
                if any(al in tname for al in tpl.aliases) or fuzz.partial_ratio(tpl.name.lower(), tname) >= 82:
                    matched_lab = lr
                    break
            score = len(matched) * 2 + (3 if urg == "urgent" else 0)
            candidates.append((score, tpl, matched, matched_lab, urg))

        candidates.sort(key=lambda x: x[0], reverse=True)
        picked = candidates[:max_items]

        # Clear older encounter investigations for clean refresh
        old = list(await db.scalars(
            select(ClinicalInvestigation).where(ClinicalInvestigation.user_id == cu.id, ClinicalInvestigation.encounter_id == encounter_id)
        ))
        for row in old:
            await db.delete(row)

        out: list[dict] = []
        for _, tpl, matched, matched_lab, urg in picked:
            in_rec = matched_lab is not None
            rec_row = ClinicalInvestigation(
                user_id=cu.id,
                encounter_id=encounter_id,
                name=tpl.name,
                purpose_category=tpl.purpose_category,
                urgency=urg,
                clinician_decision_required=True,
                rationale_enc=cu.cipher.enc(tpl.why_relevant),
                supporting_findings_enc=cu.cipher.enc(json.dumps(matched, ensure_ascii=False)),
                limitations_enc=cu.cipher.enc(tpl.limitations),
                already_in_records=in_rec,
                matched_lab_id=matched_lab.id if matched_lab else None,
            )
            db.add(rec_row)
            out.append({
                "investigation_name": tpl.name,
                "category": tpl.purpose_category,
                "why_relevant": tpl.why_relevant,
                "supporting_findings": matched,
                "urgency": urg,
                "clinician_decision_required": True,
                "limitations": tpl.limitations,
                "already_in_records": in_rec,
                "existing_record_summary": (
                    f"{matched_lab.test_name}: {matched_lab.value or matched_lab.value_text} {matched_lab.unit or ''} ({matched_lab.observed_at.strftime('%Y-%m-%d')})"
                    if matched_lab else None
                ),
            })

        await db.commit()
        return out
