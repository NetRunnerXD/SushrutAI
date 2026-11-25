"""Two-layer Patient Memory & Selective Clinical Context Builder.

Divides patient memory into:
1. Long-term patient memory: demographics, preferred language, medical history, chronic conditions,
   allergies, medication history, clinician-confirmed facts, lifestyle/social context.
2. Short-term clinical memory: active episode/encounter, current complaints, active symptoms & recent
   progression, pending follow-up questions, recent vitals, active discrepancies, unresolved concerns.

Selectively retrieves ONLY information relevant to the current conversation so the LLM prompt stays
focused, accurate, and clearly distinguishes confirmed facts vs patient-reported facts vs AI hypotheses.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Allergy,
    ClinicalEvent,
    ClinicalRecommendation,
    ClinicalSymptom,
    Condition,
    Encounter,
    FollowUpQuestion,
    MedicalHistoryEntry,
    Medication,
    PatientMemoryItem,
    PatientProfile,
    SafetyAlert,
    Vital,
)
from app.security.auth import CurrentUser


def _dec(cu: CurrentUser, blob: bytes | None) -> str | None:
    if not blob:
        return None
    try:
        return cu.cipher.dec(blob)
    except Exception:  # noqa: BLE001
        return None


def _dec_json(cu: CurrentUser, blob: bytes | None, default=None):
    raw = _dec(cu, blob)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return default


def _tag(ver: str | None) -> str:
    return {
        "clinician_confirmed": "[Confirmed]",
        "confirmed": "[Confirmed]",
        "clinician_reported": "[Clinician-reported]",
        "patient_reported": "[Patient-reported]",
        "ai_hypothesis": "[AI hypothesis - unverified]",
        "unverified": "[Unverified]",
    }.get(ver or "", "[Patient-reported]")


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[\w\u0980-\u09FF\u0900-\u097F]{3,}", (text or "").lower())}


@dataclass
class MemoryContext:
    long_term: dict = field(default_factory=dict)
    short_term: dict = field(default_factory=dict)
    prompt_block: str = ""
    returning_patient: bool = False
    prior_encounter_summary: str | None = None
    active_symptom_names: list[str] = field(default_factory=list)
    pending_questions: list[dict] = field(default_factory=list)
    discrepancies: list[str] = field(default_factory=list)
    unidentified_meds: list[str] = field(default_factory=list)


class PatientContextBuilder:
    """Selectively assembles relevant long-term and short-term clinical memory for a patient turn."""

    async def build(
        self,
        db: AsyncSession,
        cu: CurrentUser,
        *,
        query_text: str = "",
        current_symptoms: list[str] | None = None,
        encounter_id: str | None = None,
        encounter_only: bool = False,
    ) -> MemoryContext:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        q_tokens = _tokens(query_text) | {w for s in (current_symptoms or []) for w in _tokens(s)}

        # --- Long-term memory (skipped in isolated chat mode) ---
        demographics: dict = {"preferred_lang": cu.user.preferred_lang}
        conditions: list[dict] = []
        allergies: list[dict] = []
        meds_long: list[dict] = []
        relevant_history: list[dict] = []
        prior_encounters: list[Encounter] = []
        returning_patient = False

        if not encounter_only:
            p = await db.get(PatientProfile, cu.id)
            if p:
                if dob := _dec(cu, p.dob_enc):
                    demographics["dob"] = dob
                if p.sex:
                    demographics["sex"] = p.sex
                if occ := _dec(cu, p.occupation_enc):
                    demographics["occupation"] = occ
                if p.literacy_level:
                    demographics["literacy_level"] = p.literacy_level
                if p.healthcare_access:
                    demographics["healthcare_access"] = p.healthcare_access
                if p.smoking_status and p.smoking_status != "never":
                    demographics["smoking"] = p.smoking_status
                if p.pregnancy_status and p.pregnancy_status not in ("not_pregnant", "na"):
                    demographics["pregnancy_status"] = p.pregnancy_status

            conditions = [
                {"id": c.id, "name": _dec(cu, c.display_enc), "status": c.status, "verification": getattr(c, "verification_status", "patient_reported")}
                for c in await db.scalars(select(Condition).where(Condition.user_id == cu.id))
            ]
            allergies = [
                {"id": a.id, "substance": _dec(cu, a.substance_enc), "severity": a.severity, "verification": getattr(a, "verification_status", "patient_reported")}
                for a in await db.scalars(select(Allergy).where(Allergy.user_id == cu.id))
            ]
            all_meds = list(await db.scalars(select(Medication).where(Medication.user_id == cu.id)))
            meds_long = [
                {
                    "id": m.id, "name": _dec(cu, m.name_enc), "dose": m.dose, "status": m.status,
                    "prescribed_or_self": getattr(m, "prescribed_or_self", "unknown"),
                    "effectiveness": getattr(m, "reported_effectiveness", None),
                    "verification": getattr(m, "verification_status", "patient_reported"),
                }
                for m in all_meds
            ]
            hist_rows = list(await db.scalars(
                select(MedicalHistoryEntry).where(MedicalHistoryEntry.user_id == cu.id).order_by(MedicalHistoryEntry.recorded_at.desc()).limit(25)
            ))
            relevant_history = [
                {
                    "id": h.id, "category": h.category, "description": _dec(cu, h.description_enc),
                    "status": h.status, "verification": h.verification_status,
                }
                for h in hist_rows
                if not q_tokens
                or h.category in ("chronic_condition", "diagnosis")
                or (_tokens(_dec(cu, h.description_enc) or "") & q_tokens)
            ][:10]

            encounters = list(await db.scalars(
                select(Encounter).where(Encounter.user_id == cu.id).order_by(Encounter.started_at.desc()).limit(15)
            ))
            prior_encounters = [e for e in encounters if e.id != encounter_id]
            returning_patient = len(prior_encounters) > 0

        sym_q = select(ClinicalSymptom).where(ClinicalSymptom.user_id == cu.id)
        if encounter_only and encounter_id:
            sym_q = sym_q.where(ClinicalSymptom.encounter_id == encounter_id)
        elif encounter_only:
            sym_q = sym_q.where(ClinicalSymptom.encounter_id == "__none__")
        all_symptoms = list(await db.scalars(sym_q.order_by(ClinicalSymptom.last_reported_at.desc()).limit(30)))

        def _sym_relevance(s: ClinicalSymptom) -> tuple[int, datetime]:
            score = 0
            if s.status in ("active", "worsening", "improving"):
                score += 3
            if s.encounter_id == encounter_id:
                score += 4
            if q_tokens and (_tokens(s.name) & q_tokens or _tokens(s.normalized_concept) & q_tokens):
                score += 5
            if (now - s.last_reported_at) <= timedelta(days=14):
                score += 2
            return (score, s.last_reported_at)

        ranked_symptoms = sorted(all_symptoms, key=_sym_relevance, reverse=True)
        selected_symptoms = [s for s in ranked_symptoms if _sym_relevance(s)[0] > 0][:8]
        if not selected_symptoms:
            selected_symptoms = ranked_symptoms[:5]

        short_symptoms = [
            {
                "id": s.id,
                "name": s.name,
                "status": s.status,
                "progression": s.progression,
                "duration": s.duration_text,
                "onset": s.onset_text,
                "severity": s.severity,
                "frequency": s.frequency,
                "triggers": _dec_json(cu, s.triggers_enc, []),
                "functional_impact": _dec(cu, s.functional_impact_enc),
                "verification": s.verification_status,
                "first_reported": s.first_reported_at.strftime("%Y-%m-%d"),
                "last_reported": s.last_reported_at.strftime("%Y-%m-%d"),
            }
            for s in selected_symptoms
        ]

        # Pending & recently answered follow-up questions
        fq_q = select(FollowUpQuestion).where(FollowUpQuestion.user_id == cu.id)
        if encounter_only and encounter_id:
            fq_q = fq_q.where(FollowUpQuestion.encounter_id == encounter_id)
        elif encounter_only:
            fq_q = fq_q.where(FollowUpQuestion.encounter_id == "__none__")
        fq_rows = list(await db.scalars(
            fq_q.order_by(FollowUpQuestion.priority.asc(), FollowUpQuestion.asked_at.desc()).limit(20)
        ))
        pending_qs = [
            {
                "id": fq.id, "key": fq.question_key, "category": fq.category,
                "priority": fq.priority, "question": _dec(cu, fq.question_text_enc),
                "encounter_id": fq.encounter_id,
            }
            for fq in fq_rows if fq.status == "pending"
        ]
        answered_qs = [
            {"key": fq.question_key, "question": _dec(cu, fq.question_text_enc), "answer": _dec(cu, fq.answer_text_enc)}
            for fq in fq_rows if fq.status == "answered"
        ][:8]

        # Recent vitals (last 7 days or latest per type)
        vitals_rows = list(await db.scalars(
            select(Vital).where(Vital.user_id == cu.id).order_by(Vital.measured_at.desc()).limit(20)
        ))
        latest_vitals: dict[str, dict] = {}
        for v in vitals_rows:
            if v.type not in latest_vitals:
                latest_vitals[v.type] = {
                    "type": v.type, "value": v.value, "unit": v.unit,
                    "is_estimate": bool(getattr(v, "is_estimate", False)),
                    "at": v.measured_at.strftime("%Y-%m-%d %H:%M"),
                }

        # Recent clinical events & safety alerts
        recent_events = [
            {
                "type": ev.event_type,
                "at": ev.occurred_at.strftime("%Y-%m-%d %H:%M"),
                "verification": ev.verification_status,
                "discrepancy": ev.discrepancy_flag,
                "payload": _dec_json(cu, ev.payload_enc, {}),
            }
            for ev in await db.scalars(
                select(ClinicalEvent).where(ClinicalEvent.user_id == cu.id).order_by(ClinicalEvent.occurred_at.desc()).limit(10)
            )
        ]

        # Active discrepancies & unidentified meds from PatientMemoryItem
        mem_items = list(await db.scalars(
            select(PatientMemoryItem)
            .where(PatientMemoryItem.user_id == cu.id, PatientMemoryItem.active.is_(True))
            .order_by(PatientMemoryItem.updated_at.desc())
            .limit(25)
        ))
        discrepancies = [
            (_dec_json(cu, m.content_enc, {}) or {}).get("note", "")
            for m in mem_items if m.fact_type == "discrepancy"
        ]
        discrepancies = [d for d in discrepancies if d]
        unid_meds = [
            (_dec_json(cu, m.content_enc, {}) or {}).get("reported", "")
            for m in mem_items if m.topic_key == "unidentified_medicine"
        ]
        unid_meds = [u for u in unid_meds if u]

        # Prior encounter summary for returning patient continuity
        prior_summary = None
        if prior_encounters:
            pe = prior_encounters[0]
            cc = _dec(cu, pe.chief_complaint_enc)
            sm = _dec(cu, pe.summary_enc)
            when_str = pe.started_at.strftime("%d %b %Y")
            prior_summary = f"Previous visit on {when_str}" + (f" for {cc}" if cc else "") + (f": {sm}" if sm else "")

        safety_rows = list(await db.scalars(
            select(SafetyAlert).where(SafetyAlert.user_id == cu.id).order_by(SafetyAlert.triggered_at.desc()).limit(3)
        ))
        safety_recent = [
            {"severity": sa.severity, "rules": sa.rule_ids, "at": sa.triggered_at.strftime("%Y-%m-%d")}
            for sa in safety_rows
        ]

        # Build concise prompt block (only non-empty relevant sections)
        lines: list[str] = []
        if prior_summary:
            lines.append(f"RETURNING PATIENT CONTINUITY:\n- {prior_summary}")
        if short_symptoms:
            s_lines = []
            for s in short_symptoms:
                bits = [f"{_tag(s['verification'])} {s['name']} [{s['status']}, progression={s['progression']}]"]
                if s["duration"]:
                    bits.append(f"duration: {s['duration']}")
                if s["severity"] != "unknown":
                    bits.append(f"severity: {s['severity']}")
                if s["triggers"]:
                    bits.append(f"triggers: {', '.join(s['triggers'])}")
                if s["functional_impact"]:
                    bits.append(f"impact: {s['functional_impact']}")
                bits.append(f"first reported {s['first_reported']}")
                s_lines.append("- " + "; ".join(bits))
            lines.append("RELEVANT SYMPTOMS (LONGITUDINAL):\n" + "\n".join(s_lines))

        if conditions or relevant_history:
            h_lines = [f"- {_tag(c['verification'])} {c['name']} ({c['status']})" for c in conditions[:5]]
            h_lines += [f"- {_tag(h['verification'])} {h['category']}: {h['description']} ({h['status']})" for h in relevant_history[:5]]
            lines.append("RELEVANT MEDICAL HISTORY:\n" + "\n".join(h_lines))

        if allergies:
            lines.append("ALLERGIES:\n" + "\n".join(f"- {_tag(a['verification'])} {a['substance']}" for a in allergies))

        if meds_long:
            m_lines = [
                f"- {_tag(m['verification'])} {m['name']}"
                + (f" ({m['dose']})" if m["dose"] else "")
                + f" [{m['status']}, source={m['prescribed_or_self']}]"
                + (f", reported effect={m['effectiveness']}" if m["effectiveness"] else "")
                for m in meds_long[:6]
            ]
            lines.append("MEDICATIONS:\n" + "\n".join(m_lines))

        if latest_vitals:
            v_lines = [
                f"- {v['type']}: {v['value']} {v['unit']} ({'patient estimate' if v['is_estimate'] else 'measured'}, {v['at']})"
                for v in latest_vitals.values()
            ]
            lines.append("RECENT VITALS:\n" + "\n".join(v_lines))

        if answered_qs:
            lines.append("ALREADY ANSWERED QUESTIONS (do NOT ask again):\n" + "\n".join(
                f"- Q: {aq['question']} -> A: {aq['answer']}" for aq in answered_qs[:5]
            ))

        if discrepancies:
            lines.append("DISCREPANCIES NEEDING CLARIFICATION:\n" + "\n".join(f"- [Unresolved] {d}" for d in discrepancies[:3]))

        if safety_recent:
            lines.append("RECENT SAFETY ALERTS:\n" + "\n".join(f"- {sr['severity']} ({', '.join(sr['rules'])}) on {sr['at']}" for sr in safety_recent))

        if not encounter_only:
            try:
                from app.clinical.subject_context import load_subject_relations, retrieve_family_member_context
                from app.memory.correlator import ClinicalCorrelator
                from app.memory.neural_correlator import build_clinical_neural_graph
                from app.models import LabResult
                from app.security.settings import get_settings_row
                corr = ClinicalCorrelator()
                corr_notes = corr.correlate_meds_and_symptoms(
                    meds_long, [s["name"] for s in short_symptoms] + (current_symptoms or [])
                )
                lab_rows = list(await db.scalars(
                    select(LabResult).where(LabResult.user_id == cu.id).order_by(LabResult.observed_at.desc()).limit(20)
                ))
                corr_notes.extend(corr.correlate_labs(lab_rows))
                corr_notes.extend(await corr.correlate_household(db, cu.account_id, cu.id))
                corr_notes.extend(await retrieve_family_member_context(db, cu, query_text))
                subject_rels = await load_subject_relations(db, cu)
                for sr in subject_rels[:4]:
                    bits = [sr.get("relation", "relation")]
                    if sr.get("age") is not None:
                        bits.append(f"age {sr['age']}")
                    if sr.get("gender"):
                        bits.append(str(sr["gender"]))
                    if sr.get("notes"):
                        bits.append(f"context: {'; '.join(sr['notes'][:2])}")
                    corr_notes.append("Subject relation context: " + ", ".join(bits))

                settings_row = await get_settings_row(db, cu.account_id)
                if getattr(settings_row, "neural_correlation_enabled", True):
                    lab_dicts = [
                        {"test_name": lb.test_name, "value": lb.value, "unit": lb.unit, "flag": lb.flag}
                        for lb in lab_rows
                    ]
                    ng = build_clinical_neural_graph(
                        symptoms=short_symptoms,
                        medications=meds_long,
                        conditions=conditions,
                        vitals=list(latest_vitals.values()),
                        labs=lab_dicts,
                        correlation_notes=corr_notes,
                        subject_relations=subject_rels,
                        followup_questions=pending_qs,
                    )
                    if ng.get("nodes"):
                        top_nodes = sorted(ng["nodes"], key=lambda n: n.get("score", 0.0), reverse=True)[:3]
                        top_str = ", ".join(f"{n['label']} ({n['score']})" for n in top_nodes)
                        corr_notes.append(
                            f"Neural Correlation ({'GPU' if ng.get('gpu_boosted') else 'CPU'}, "
                            f"temporal={ng.get('temporal_score')}, motif={ng.get('cnn_motif_score')}): "
                            f"top signals [{top_str}]"
                        )
                        for cust in ng.get("customizations", [])[:2]:
                            corr_notes.append(f"Response customization: {cust}")

                if corr_notes:
                    lines.append("CLINICAL CORRELATIONS & HOUSEHOLD SIGNALS:\n" + "\n".join(f"- {n}" for n in corr_notes[:8]))
            except Exception:  # noqa: BLE001 — correlation never breaks context build
                pass

        return MemoryContext(
            long_term={
                "demographics": demographics,
                "conditions": conditions,
                "allergies": allergies,
                "medications": meds_long,
                "medical_history": relevant_history,
            },
            short_term={
                "active_symptoms": short_symptoms,
                "pending_questions": pending_qs,
                "answered_questions": answered_qs,
                "latest_vitals": list(latest_vitals.values()),
                "recent_events": recent_events,
                "discrepancies": discrepancies,
                "unidentified_medications": unid_meds,
                "safety_alerts": safety_recent,
            },
            prompt_block="\n\n".join(lines),
            returning_patient=returning_patient,
            prior_encounter_summary=prior_summary,
            active_symptom_names=[s["name"] for s in short_symptoms if s["status"] != "resolved"],
            pending_questions=pending_qs,
            discrepancies=discrepancies,
            unidentified_meds=unid_meds,
        )
