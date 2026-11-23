"""ClinicalReasoningEngine: maintains an evolving differential assessment per encounter.

Separates established/patient-reported/measured/clinician-confirmed facts from AI-inferred hypotheses,
prevents anchoring on the initial guess, updates supporting/against/missing evidence dynamically when
new facts or patient corrections arrive, and answers causal patient questions transparently.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clinical.schema import ClinicalExtractionPayload
from app.memory.context import MemoryContext
from app.models import ClinicalAssessment, DifferentialConsideration
from app.safety.clinical_safety import SafetyAssessment
from app.security.auth import CurrentUser


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


_CAUSAL_QUERY = re.compile(
    r"(?i)(?:could\s+this\s+be|might\s+(?:this|it)\s+be|is\s+(?:this|it)\s+(?:because\s+of|due\s+to)|"
    r"can\s+.{1,40}\s+cause\s+this|because\s+of|নিম্ন\s+রক্তচাপ|লো\s*প্রেশার|কম\s*রক্তচাপ|হতে\s+পারে)"
)
_CORRECTION_CUE = re.compile(
    r"(?i)\b(?:no,?\s+(?:actually|it\s+also|it\s+happens|not\s+only)|actually,?\s+it|even\s+when\s+(?:sitting|lying|resting)|also\s+when\s+sitting)\b|"
    r"না,?\s*বসে\s+থাকলেও|শুয়ে\s+থাকলেও|আসলে"
)
_SITTING_CUE = re.compile(r"(?i)\b(?:sitting|lying\s+down|at\s+rest|even\s+resting)\b|বসে\s+থাক|শুয়ে\s+থাক")


@dataclass
class HypothesisSpec:
    name: str
    triggers: tuple[str, ...]
    cues: tuple[str, ...]
    against_cues: tuple[str, ...]
    missing_checks: tuple[str, ...]
    next_step: str


# Clinical reasoning knowledge table (initial candidate considerations; never auto-diagnosed)
_HYPOTHESIS_CATALOG: tuple[HypothesisSpec, ...] = (
    HypothesisSpec(
        name="Orthostatic / postural blood pressure drop (orthostatic hypotension)",
        triggers=("standing up",),
        cues=("spinning", "dizziness", "lightheaded", "standing", "weakness", "ঘোরে", "দাঁড়ালে", "দুর্বল"),
        against_cues=("while sitting", "even when sitting", "at rest", "turning head in bed", "বসে থাকলেও"),
        missing_checks=("Lying vs standing blood pressure reading", "Hydration & oral intake history", "Current BP/diabetes medicines"),
        next_step="Measure blood pressure and pulse while lying down and after standing for 1-3 minutes",
    ),
    HypothesisSpec(
        name="Positional inner-ear vertigo (e.g., BPPV)",
        triggers=("turning head", "rolling over in bed", "head movement"),
        cues=("turning head", "rolling", "spinning", "bed", "seconds", "মাথা ঘোর", "পাশ ফিরলে"),
        against_cues=("chest pain", "fainted", "blackout"),
        missing_checks=("Whether spinning stops within 1 minute of keeping head still", "Any ear fullness, ringing, or hearing change"),
        next_step="Clinical vestibular examination (e.g., Dix-Hallpike assessment by a doctor)",
    ),
    HypothesisSpec(
        name="Anaemia / low haemoglobin or nutritional deficiency",
        triggers=(),
        cues=("weakness", "fatigue", "head heaviness", "days", "cannot work", "breathless", "দুর্বল", "কাজ করতে পারছি না"),
        against_cues=(),
        missing_checks=("Pallor / conjunctival check", "Diet & heavy menstrual/GI blood loss history", "Haemoglobin / CBC"),
        next_step="Discuss Complete Blood Count (CBC) and iron/B12 check with a doctor",
    ),
    HypothesisSpec(
        name="Hypoglycaemia or blood sugar fluctuation",
        triggers=("missed meal", "fasting"),
        cues=("weakness", "dizziness", "sweating", "shakiness", "diabetes", "metformin", "glimepiride", "insulin", "চিনি", "কমজোরি"),
        against_cues=(),
        missing_checks=("Fingerstick blood glucose during symptoms", "Timing relative to meals and medicines"),
        next_step="Check blood glucose immediately when symptoms occur and review diabetes medicines",
    ),
    HypothesisSpec(
        name="Systemic viral or febrile illness (e.g., dengue, typhoid, viral fever)",
        triggers=(),
        cues=("fever", "not feeling well", "body ache", "head heaviness", "chill", "জ্বর", "শরীর ভালো লাগছে না"),
        against_cues=(),
        missing_checks=("Measured temperature curve", "Rash, bleeding gums, severe abdominal pain, or travel/mosquito exposure"),
        next_step="See a doctor within 24h for temperature evaluation and fever workup if fever ≥3 days",
    ),
    HypothesisSpec(
        name="Dehydration / volume depletion",
        triggers=("exertion", "heat"),
        cues=("poor intake", "weakness", "dizziness", "fields", "vomiting", "diarrhea", "জল কম", "বমি"),
        against_cues=(),
        missing_checks=("Daily water/fluid intake & urine output", "Standing heart rate & BP"),
        next_step="Assess hydration status, electrolytes, and resting/standing vital signs",
    ),
    HypothesisSpec(
        name="Cardiovascular / arrhythmia evaluation warranted",
        triggers=(),
        cues=("chest pain", "palpitation", "fainting", "shortness of breath", "বুক ধড়ফড়", "অজ্ঞান"),
        against_cues=(),
        missing_checks=("12-lead ECG", "Urgent physical cardiac exam"),
        next_step="Urgent clinical evaluation and 12-lead ECG",
    ),
)


class ClinicalReasoningEngine:
    """Dynamically builds and updates encounter clinical assessments & differentials."""

    async def evaluate_and_persist(
        self,
        db: AsyncSession,
        cu: CurrentUser,
        *,
        encounter_id: str,
        user_text: str,
        extraction: ClinicalExtractionPayload,
        mem: MemoryContext,
        safety: SafetyAssessment,
    ) -> dict:
        now = _now()
        # Combine text, active symptoms, triggers, history, and prior encounter summary into a unified reasoning signal
        sym_names = [s.name for s in extraction.symptoms] + mem.active_symptom_names
        neg_names = {n.name.lower() for n in extraction.negated_symptoms} | {n.lower() for n in safety.negative_findings}
        all_triggers = [t for s in extraction.symptoms for t in s.triggers]
        for s in mem.short_term.get("active_symptoms", []):
            all_triggers.extend(s.get("triggers") or [])

        signal_text = " ".join([
            user_text or "",
            " ".join(sym_names),
            " ".join(all_triggers),
            " ".join(m.get("name", "") for m in mem.long_term.get("medications", []) if m.get("name")),
            " ".join(c.get("name", "") for c in mem.long_term.get("conditions", []) if c.get("name")),
        ]).lower()

        is_correction = bool(_CORRECTION_CUE.search(user_text or ""))
        sitting_present = bool(_SITTING_CUE.search(user_text or ""))
        is_causal_q = bool(_CAUSAL_QUERY.search(user_text or ""))

        # Deactivate prior differential rows on this encounter so current assessment reflects latest evidence cleanly
        old_diffs = list(await db.scalars(
            select(DifferentialConsideration).where(
                DifferentialConsideration.user_id == cu.id,
                DifferentialConsideration.encounter_id == encounter_id,
                DifferentialConsideration.active.is_(True),
            )
        ))
        old_ver_count = len(list(await db.scalars(
            select(ClinicalAssessment.id).where(ClinicalAssessment.encounter_id == encounter_id)
        )))

        old_diff_map = {d.explanation: d for d in old_diffs}

        scored: list[tuple[int, HypothesisSpec, list[str], list[str], list[str]]] = []
        for spec in _HYPOTHESIS_CATALOG:
            supporting: list[str] = []
            against: list[str] = []
            for cue in spec.cues:
                if cue in signal_text:
                    supporting.append(f"Reported: {cue}")
            for tr in spec.triggers:
                if tr in signal_text:
                    supporting.append(f"Trigger matches: {tr}")
            # If patient asks explicitly ("Could this be because of low blood pressure?"), include BP hypothesis for explanation
            if is_causal_q and ("blood pressure" in signal_text or "bp" in signal_text) and "blood pressure" in spec.name.lower():
                supporting.append("Patient specifically asked about blood pressure")

            for ag in spec.against_cues:
                if ag in signal_text or (ag == "while sitting" and sitting_present):
                    against.append(f"Patient noted occurrence {ag}")
            for neg in neg_names:
                if neg in spec.cues:
                    against.append(f"Patient denied {neg}")

            prev_row = old_diff_map.get(spec.name)
            if not supporting and prev_row is not None and against:
                prior_sup = json.loads(cu.cipher.dec(prev_row.supporting_evidence_enc) or "[]")
                supporting.extend(prior_sup[:2] or ["Previously considered"])

            if not supporting:
                continue
            score = len(supporting) * 2 - len(against) * 2
            scored.append((score, spec, list(dict.fromkeys(supporting)), list(dict.fromkeys(against)), list(spec.missing_checks)))

        scored.sort(key=lambda x: x[0], reverse=True)
        top_specs = scored[:5]

        established = [
            f"{m.get('name') or m.get('description')}"
            for m in mem.long_term.get("conditions", [])
            if m.get("verification") in ("CLINICIAN_CONFIRMED", "clinician_confirmed", "confirmed")
        ]
        missing_all = list(dict.fromkeys(
            [m.reason for m in extraction.missing_information]
            + [chk for _, _, _, _, checks in top_specs[:3] for chk in checks]
        ))[:8]

        assess_summary = (
            f"Safety status: {safety.severity}. "
            + (f"Correction integrated (non-postural symptoms noted). " if (is_correction and sitting_present) else "")
            + (f"Top considerations (unconfirmed): {', '.join(sp.name.split(' (')[0] for _, sp, *_ in top_specs[:3])}." if top_specs else "Gathering additional clinical context.")
        )
        assessment = ClinicalAssessment(
            user_id=cu.id,
            encounter_id=encounter_id,
            version_no=old_ver_count + 1,
            summary_enc=cu.cipher.enc(assess_summary),
            established_facts_enc=cu.cipher.enc(json.dumps({
                "clinician_confirmed": established,
                "patient_reported": safety.positive_findings,
                "patient_denied": safety.negative_findings,
                "discrepancies": mem.discrepancies,
            }, ensure_ascii=False)),
            missing_evidence_enc=cu.cipher.enc(json.dumps(missing_all, ensure_ascii=False)),
            safety_status=safety.severity,
            interview_outcome="URGENT_ESCALATION" if safety.escalate_now else "ADDITIONAL_INFORMATION_REQUIRED" if missing_all else "SUFFICIENT_INFORMATION",
            verification_status="AI_INFERRED",
        )
        db.add(assessment)
        await db.flush()

        for od in old_diffs:
            od.active = False

        differentials_out: list[dict] = []
        for score, spec, sup, ag, miss in top_specs:
            band = "more_likely" if score >= 4 else "possible" if score >= 1 else "less_likely"
            row = DifferentialConsideration(
                user_id=cu.id,
                encounter_id=encounter_id,
                assessment_id=assessment.id,
                explanation=spec.name[:180],
                supporting_evidence_enc=cu.cipher.enc(json.dumps(sup, ensure_ascii=False)),
                against_evidence_enc=cu.cipher.enc(json.dumps(ag, ensure_ascii=False)),
                missing_evidence_enc=cu.cipher.enc(json.dumps(miss, ensure_ascii=False)),
                next_assessment=spec.next_step[:240],
                likelihood_band=band,
                review_status="AI_INFERRED",
                active=True,
            )
            db.add(row)
            differentials_out.append({
                "explanation": spec.name,
                "supporting_evidence": sup,
                "evidence_against": ag,
                "missing_evidence": miss,
                "next_assessment": spec.next_step,
                "likelihood_band": band,
                "clinical_review_status": "AI_INFERRED (not a confirmed diagnosis)",
            })

        await db.commit()

        prompt_block = self.format_prompt_block(
            differentials=differentials_out,
            established=established,
            safety=safety,
            missing=missing_all,
            discrepancies=mem.discrepancies,
            is_correction=is_correction,
            is_causal_q=is_causal_q,
        )
        return {
            "assessment_id": assessment.id,
            "version_no": assessment.version_no,
            "summary": assess_summary,
            "safety_status": safety.severity,
            "established_facts": {
                "clinician_confirmed": established,
                "patient_reported": safety.positive_findings,
                "patient_denied": safety.negative_findings,
                "unknown": safety.unknown_findings,
                "not_assessed": safety.not_assessed,
            },
            "differentials": differentials_out,
            "missing_evidence": missing_all,
            "contradictions": mem.discrepancies,
            "correction_detected": is_correction,
            "causal_question_detected": is_causal_q,
            "prompt_block": prompt_block,
        }

    @staticmethod
    def format_prompt_block(
        *,
        differentials: list[dict],
        established: list[str],
        safety: SafetyAssessment,
        missing: list[str],
        discrepancies: list[str],
        is_correction: bool,
        is_causal_q: bool,
    ) -> str:
        lines = ["CLINICAL REASONING STATE (evolving; never present possibilities as confirmed diagnosis):"]
        if is_correction:
            lines.append("- PATIENT CORRECTION JUST RECEIVED: Acknowledge the correction directly, revise prior assumptions, and do NOT repeat contradicted conclusions.")
        if is_causal_q:
            lines.append("- PATIENT ASKED ABOUT A SPECIFIC CAUSE: Address that possibility directly using supporting facts, missing evidence, and what measurement helps check it, without confirming it.")
        if established:
            lines.append(f"- Clinician-confirmed baseline: {', '.join(established)}")
        if safety.positive_findings:
            lines.append(f"- Patient-reported present: {', '.join(safety.positive_findings)}")
        if safety.negative_findings:
            lines.append(f"- Patient-reported absent: {', '.join(safety.negative_findings)}")
        if missing:
            lines.append(f"- Key missing evidence: {'; '.join(missing[:5])}")
        if discrepancies:
            lines.append(f"- Unresolved contradictions: {'; '.join(discrepancies[:3])}")
        for d in differentials[:4]:
            lines.append(
                f"- Possibility ({d['likelihood_band']}, unconfirmed): {d['explanation']} | "
                f"For: {', '.join(d['supporting_evidence'][:3])} | "
                + (f"Against: {', '.join(d['evidence_against'][:2])} | " if d["evidence_against"] else "")
                + f"Missing: {', '.join(d['missing_evidence'][:2])} | Next check: {d['next_assessment']}"
            )
        return "\n".join(lines)
