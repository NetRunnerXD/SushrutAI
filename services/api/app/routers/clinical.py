"""Longitudinal Clinical Intelligence API endpoints.

Provides patient-isolated access to current health status, longitudinal timeline,
consultation encounters (preserving raw & normalized transcripts), structured extraction
preview, follow-up response recording, and auditable clinical record confirmation/correction.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clinical.extract import extract_clinical_turn
from app.clinical.followup import next_followup_question, record_followup_answer
from app.clinical.persistence import confirm_or_correct_record, persist_extraction
from app.db import get_db
from app.memory.context import PatientContextBuilder
from app.models import (
    Allergy,
    ClinicalEvent,
    ClinicalRecommendation,
    ClinicalSymptom,
    Condition,
    ConversationTurn,
    Encounter,
    FollowUpQuestion,
    MedicalHistoryEntry,
    Medication,
    PatientMemoryItem,
    SafetyAlert,
    SymptomObservation,
    Vital,
)
from app.safety.clinical_safety import evaluate_safety
from app.safety.disclaimer import disclaimer
from app.security.auth import CurrentUser, current_user
from app.speech.normalize import analyze_transcript

log = logging.getLogger("sushrut.clinical.router")
router = APIRouter(prefix="/clinical", tags=["clinical"])


def _dec(cu: CurrentUser, blob: bytes | None) -> str | None:
    if not blob:
        return None
    try:
        return cu.cipher.dec(blob)
    except Exception:  # noqa: BLE001
        return None


def _dec_json(cu: CurrentUser, blob: bytes | None, default: Any = None) -> Any:
    raw = _dec(cu, blob)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return default


def _symptom_out(cu: CurrentUser, sx: ClinicalSymptom, discrepant_ids: set[str] | None = None) -> dict:
    return {
        "id": sx.id,
        "encounter_id": sx.encounter_id,
        "name": sx.name,
        "normalized_concept": sx.normalized_concept,
        "anatomical_location": sx.anatomical_location,
        "status": sx.status,
        "progression": sx.progression,
        "onset_text": sx.onset_text,
        "estimated_onset": sx.estimated_onset.isoformat() if sx.estimated_onset else None,
        "onset_approximate": sx.onset_approximate,
        "duration_text": sx.duration_text,
        "frequency": sx.frequency,
        "severity": sx.severity,
        "character": sx.character,
        "functional_impact": _dec(cu, sx.functional_impact_enc),
        "triggers": _dec_json(cu, sx.triggers_enc, []),
        "relieving_factors": _dec_json(cu, sx.relieving_factors_enc, []),
        "associated_symptoms": _dec_json(cu, sx.associated_symptoms_enc, []),
        "original_statement": _dec(cu, sx.original_statement_enc),
        "confidence": sx.confidence,
        "verification_status": sx.verification_status,
        "source": sx.source,
        "has_discrepancy": sx.id in (discrepant_ids or set()),
        "first_reported_at": sx.first_reported_at.isoformat() if sx.first_reported_at else None,
        "last_reported_at": sx.last_reported_at.isoformat() if sx.last_reported_at else None,
        "resolved_at": sx.resolved_at.isoformat() if sx.resolved_at else None,
    }


@router.get("/status")
async def clinical_status(
    lang: str = Query(default="en"),
    cu: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Patient-isolated current health status: active & resolved symptoms, pending follow-ups,
    medications, allergies, medical history, active discrepancies, and latest guidance."""
    mem = await PatientContextBuilder().build(db, cu, query_text="current status")

    syms = list(
        await db.scalars(
            select(ClinicalSymptom)
            .where(ClinicalSymptom.user_id == cu.id)
            .order_by(ClinicalSymptom.last_reported_at.desc())
        )
    )
    contradictory_obs = list(
        await db.scalars(
            select(SymptomObservation.symptom_id)
            .where(SymptomObservation.user_id == cu.id, SymptomObservation.is_contradictory.is_(True))
        )
    )
    discrepant_ids = set(contradictory_obs)

    active_symptoms = [_symptom_out(cu, s, discrepant_ids) for s in syms if s.status != "resolved"]
    resolved_symptoms = [_symptom_out(cu, s, discrepant_ids) for s in syms if s.status == "resolved"][:20]

    pending_fqs = list(
        await db.scalars(
            select(FollowUpQuestion)
            .where(FollowUpQuestion.user_id == cu.id, FollowUpQuestion.status == "pending")
            .order_by(FollowUpQuestion.priority.asc(), FollowUpQuestion.asked_at.desc())
        )
    )

    meds = list(await db.scalars(select(Medication).where(Medication.user_id == cu.id)))
    allergies = list(await db.scalars(select(Allergy).where(Allergy.user_id == cu.id)))
    conditions = list(await db.scalars(select(Condition).where(Condition.user_id == cu.id)))
    history_rows = list(
        await db.scalars(
            select(MedicalHistoryEntry)
            .where(MedicalHistoryEntry.user_id == cu.id)
            .order_by(MedicalHistoryEntry.recorded_at.desc())
        )
    )

    discrepancy_rows = list(
        await db.scalars(
            select(PatientMemoryItem)
            .where(
                PatientMemoryItem.user_id == cu.id,
                PatientMemoryItem.fact_type == "discrepancy",
                PatientMemoryItem.active.is_(True),
            )
            .order_by(PatientMemoryItem.created_at.desc())
        )
    )

    alerts = list(
        await db.scalars(
            select(SafetyAlert)
            .where(SafetyAlert.user_id == cu.id)
            .order_by(SafetyAlert.triggered_at.desc())
            .limit(10)
        )
    )

    last_rec = await db.scalar(
        select(ClinicalRecommendation)
        .where(ClinicalRecommendation.user_id == cu.id)
        .order_by(ClinicalRecommendation.created_at.desc())
        .limit(1)
    )

    return {
        "patient_id": cu.id,
        "disclaimer": disclaimer(lang),
        "returning_patient": mem.returning_patient,
        "prior_encounter_summary": mem.prior_encounter_summary,
        "active_symptoms": active_symptoms,
        "resolved_symptoms": resolved_symptoms,
        "pending_followups": [
            {
                "id": f.id,
                "encounter_id": f.encounter_id,
                "question_key": f.question_key,
                "category": f.category,
                "priority": f.priority,
                "question": _dec(cu, f.question_text_enc),
                "reason": f.reason,
                "asked_at": f.asked_at.isoformat() if f.asked_at else None,
            }
            for f in pending_fqs
        ],
        "medications": [
            {
                "id": m.id,
                "name": _dec(cu, m.name_enc) or "",
                "dose": m.dose,
                "freq": m.frequency.get("label") if isinstance(m.frequency, dict) else m.frequency,
                "frequency": m.frequency,
                "route": m.route,
                "status": m.status,
                "unidentified": "unidentified" in (_dec(cu, m.name_enc) or "").lower(),
                "verification_status": m.verification_status or "patient_reported",
                "source": m.source,
            }
            for m in meds
        ],
        "allergies": [
            {
                "id": a.id,
                "substance": _dec(cu, a.substance_enc) or "",
                "severity": a.severity,
                "verification_status": a.verification_status or "patient_reported",
            }
            for a in allergies
        ],
        "conditions": [
            {
                "id": c.id,
                "display": _dec(cu, c.display_enc) or "",
                "status": c.status,
                "verification_status": c.verification_status or "patient_reported",
            }
            for c in conditions
        ],
        "medical_history": [
            {
                "id": h.id,
                "category": h.category,
                "normalized_name": h.normalized_name,
                "description": _dec(cu, h.description_enc) or "",
                "status": h.status,
                "onset_or_date_text": h.onset_or_date_text,
                "verification_status": h.verification_status,
                "source": h.source,
            }
            for h in history_rows
        ],
        "discrepancies": [
            {"id": d.id, "topic": d.topic_key, "detail": _dec_json(cu, d.content_enc, {})}
            for d in discrepancy_rows
        ],
        "safety_alerts": [
            {
                "id": al.id,
                "severity": al.severity,
                "rule_ids": al.rule_ids,
                "guidance": _dec(cu, al.guidance_enc) or "",
                "call_numbers": al.call_numbers,
                "triggered_at": al.triggered_at.isoformat() if al.triggered_at else None,
            }
            for al in alerts
        ],
        "last_recommendation": (
            {
                "id": last_rec.id,
                "encounter_id": last_rec.encounter_id,
                "urgency": last_rec.urgency,
                "specialist": last_rec.specialist,
                "summary": _dec(cu, last_rec.summary_enc) or "",
                "self_care": _dec_json(cu, last_rec.self_care_enc, []),
                "watch_for": _dec_json(cu, last_rec.watch_for_enc, []),
                "verification_status": last_rec.verification_status,
                "created_at": last_rec.created_at.isoformat() if last_rec.created_at else None,
            }
            if last_rec
            else None
        ),
        "long_term_memory": mem.long_term,
    }


@router.get("/timeline")
async def clinical_timeline(
    event_type: str | None = Query(default=None),
    order: Literal["desc", "asc"] = Query(default="desc"),
    limit: int = Query(default=150, ge=1, le=500),
    cu: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Chronological longitudinal timeline of clinical events, observations, vitals, and escalations."""
    q = select(ClinicalEvent).where(ClinicalEvent.user_id == cu.id)
    if event_type:
        q = q.where(ClinicalEvent.event_type == event_type)
    q = q.order_by(desc(ClinicalEvent.occurred_at) if order == "desc" else ClinicalEvent.occurred_at.asc()).limit(limit)
    events = list(await db.scalars(q))

    observations = list(
        await db.scalars(
            select(SymptomObservation)
            .where(SymptomObservation.user_id == cu.id)
            .order_by(desc(SymptomObservation.observed_at) if order == "desc" else SymptomObservation.observed_at.asc())
            .limit(100)
        )
    )
    sym_names = {
        s.id: s.name
        for s in await db.scalars(select(ClinicalSymptom).where(ClinicalSymptom.user_id == cu.id))
    }

    vitals = list(
        await db.scalars(
            select(Vital)
            .where(Vital.user_id == cu.id)
            .order_by(desc(Vital.measured_at) if order == "desc" else Vital.measured_at.asc())
            .limit(80)
        )
    )

    return {
        "patient_id": cu.id,
        "events": [
            {
                "id": ev.id,
                "encounter_id": ev.encounter_id,
                "event_type": ev.event_type,
                "entity_type": ev.entity_type,
                "entity_id": ev.entity_id,
                "source": ev.source,
                "verification_status": ev.verification_status,
                "discrepancy_flag": ev.discrepancy_flag,
                "payload": _dec_json(cu, ev.payload_enc, {}),
                "occurred_at": ev.occurred_at.isoformat() if ev.occurred_at else None,
            }
            for ev in events
        ],
        "observations": [
            {
                "id": ob.id,
                "symptom_id": ob.symptom_id,
                "symptom_name": sym_names.get(ob.symptom_id, "symptom"),
                "encounter_id": ob.encounter_id,
                "observed_at": ob.observed_at.isoformat() if ob.observed_at else None,
                "reported_status": ob.reported_status,
                "progression": ob.progression,
                "severity": ob.severity,
                "duration_text": ob.duration_text,
                "frequency": ob.frequency,
                "triggers": _dec_json(cu, ob.triggers_enc, []),
                "relieving_factors": _dec_json(cu, ob.relieving_factors_enc, []),
                "functional_impact": _dec(cu, ob.functional_impact_enc),
                "original_statement": _dec(cu, ob.original_statement_enc) or "",
                "is_contradictory": ob.is_contradictory,
                "discrepancy_note": _dec(cu, ob.discrepancy_note_enc),
                "confidence": ob.confidence,
                "verification_status": ob.verification_status,
                "source": ob.source,
            }
            for ob in observations
        ],
        "vitals": [
            {
                "id": v.id,
                "type": v.type,
                "value": v.value,
                "unit": v.unit,
                "source": v.source,
                "is_estimate": bool(v.is_estimate),
                "original_statement": _dec(cu, v.original_statement_enc),
                "measured_at": v.measured_at.isoformat() if v.measured_at else None,
            }
            for v in vitals
        ],
    }


@router.get("/encounters")
async def list_encounters(
    limit: int = Query(default=30, ge=1, le=100),
    cu: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    encs = list(
        await db.scalars(
            select(Encounter)
            .where(Encounter.user_id == cu.id)
            .order_by(Encounter.started_at.desc())
            .limit(limit)
        )
    )
    out = []
    for e in encs:
        turn_count = len(list(await db.scalars(select(ConversationTurn.id).where(ConversationTurn.encounter_id == e.id))))
        fq_count = len(list(await db.scalars(select(FollowUpQuestion.id).where(FollowUpQuestion.encounter_id == e.id))))
        out.append({
            "id": e.id,
            "chat_session_id": e.chat_session_id,
            "status": e.status,
            "channel": e.channel,
            "language": e.language,
            "chief_complaint": _dec(cu, e.chief_complaint_enc),
            "summary": _dec(cu, e.summary_enc),
            "urgency": e.urgency,
            "specialist": e.specialist,
            "turn_count": turn_count,
            "followup_count": fq_count,
            "started_at": e.started_at.isoformat() if e.started_at else None,
            "ended_at": e.ended_at.isoformat() if e.ended_at else None,
        })
    return {"encounters": out}


@router.get("/encounters/{eid}")
async def encounter_detail(
    eid: str,
    cu: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    e = await db.get(Encounter, eid)
    if e is None or e.user_id != cu.id:
        raise HTTPException(404, "Encounter not found")

    turns = list(
        await db.scalars(
            select(ConversationTurn)
            .where(ConversationTurn.user_id == cu.id, ConversationTurn.encounter_id == eid)
            .order_by(ConversationTurn.turn_index.asc())
        )
    )
    fqs = list(
        await db.scalars(
            select(FollowUpQuestion)
            .where(FollowUpQuestion.user_id == cu.id, FollowUpQuestion.encounter_id == eid)
            .order_by(FollowUpQuestion.asked_at.asc())
        )
    )
    obs = list(
        await db.scalars(
            select(SymptomObservation)
            .where(SymptomObservation.user_id == cu.id, SymptomObservation.encounter_id == eid)
            .order_by(SymptomObservation.observed_at.asc())
        )
    )
    alerts = list(
        await db.scalars(
            select(SafetyAlert)
            .where(SafetyAlert.user_id == cu.id, SafetyAlert.encounter_id == eid)
            .order_by(SafetyAlert.triggered_at.asc())
        )
    )
    recs = list(
        await db.scalars(
            select(ClinicalRecommendation)
            .where(ClinicalRecommendation.user_id == cu.id, ClinicalRecommendation.encounter_id == eid)
            .order_by(ClinicalRecommendation.created_at.desc())
        )
    )
    sym_names = {
        s.id: s.name
        for s in await db.scalars(select(ClinicalSymptom).where(ClinicalSymptom.user_id == cu.id))
    }

    return {
        "encounter": {
            "id": e.id,
            "chat_session_id": e.chat_session_id,
            "status": e.status,
            "channel": e.channel,
            "language": e.language,
            "chief_complaint": _dec(cu, e.chief_complaint_enc),
            "summary": _dec(cu, e.summary_enc),
            "urgency": e.urgency,
            "specialist": e.specialist,
            "started_at": e.started_at.isoformat() if e.started_at else None,
            "ended_at": e.ended_at.isoformat() if e.ended_at else None,
        },
        "turns": [
            {
                "id": t.id,
                "turn_index": t.turn_index,
                "role": t.role,
                "raw_transcript": _dec(cu, t.raw_transcript_enc) or "",
                "normalized_transcript": _dec(cu, t.normalized_transcript_enc),
                "detected_lang": t.detected_lang,
                "code_switched": t.code_switched,
                "asr_confidence": t.asr_confidence,
                "asr_engine": t.asr_engine,
                "speech_segments": t.speech_segments,
                "extracted_payload": _dec_json(cu, t.extracted_payload_enc),
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in turns
        ],
        "followups": [
            {
                "id": fq.id,
                "question_key": fq.question_key,
                "category": fq.category,
                "priority": fq.priority,
                "question": _dec(cu, fq.question_text_enc) or "",
                "answer": _dec(cu, fq.answer_text_enc),
                "status": fq.status,
                "reason": fq.reason,
                "asked_at": fq.asked_at.isoformat() if fq.asked_at else None,
                "answered_at": fq.answered_at.isoformat() if fq.answered_at else None,
            }
            for fq in fqs
        ],
        "observations": [
            {
                "id": ob.id,
                "symptom_id": ob.symptom_id,
                "symptom_name": sym_names.get(ob.symptom_id, "symptom"),
                "reported_status": ob.reported_status,
                "progression": ob.progression,
                "severity": ob.severity,
                "duration_text": ob.duration_text,
                "frequency": ob.frequency,
                "triggers": _dec_json(cu, ob.triggers_enc, []),
                "functional_impact": _dec(cu, ob.functional_impact_enc),
                "original_statement": _dec(cu, ob.original_statement_enc) or "",
                "is_contradictory": ob.is_contradictory,
                "discrepancy_note": _dec(cu, ob.discrepancy_note_enc),
                "verification_status": ob.verification_status,
                "observed_at": ob.observed_at.isoformat() if ob.observed_at else None,
            }
            for ob in obs
        ],
        "safety_alerts": [
            {
                "id": al.id,
                "severity": al.severity,
                "rule_ids": al.rule_ids,
                "guidance": _dec(cu, al.guidance_enc) or "",
                "call_numbers": al.call_numbers,
                "triggered_at": al.triggered_at.isoformat() if al.triggered_at else None,
            }
            for al in alerts
        ],
        "recommendations": [
            {
                "id": r.id,
                "urgency": r.urgency,
                "specialist": r.specialist,
                "summary": _dec(cu, r.summary_enc) or "",
                "self_care": _dec_json(cu, r.self_care_enc, []),
                "watch_for": _dec_json(cu, r.watch_for_enc, []),
                "verification_status": r.verification_status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in recs
        ],
    }


class ExtractIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    lang: str = "en"
    encounter_id: str | None = None
    use_llm: bool = False
    reference_iso: str | None = None


@router.post("/extract")
async def preview_extract(body: ExtractIn, cu: CurrentUser = Depends(current_user)):
    """Run transcript normalization, clinical extraction, temporal parsing, and safety evaluation
    without writing directly to persistent storage."""
    ref_dt = None
    if body.reference_iso:
        try:
            ref_dt = datetime.fromisoformat(body.reference_iso.rstrip("Z")).replace(tzinfo=None)
        except ValueError:
            raise HTTPException(422, "Invalid reference_iso timestamp")
    analysis = analyze_transcript(body.text, lang_hint=body.lang)
    payload = await extract_clinical_turn(
        analysis.normalized_transcript or body.text,
        patient_id=cu.id,
        encounter_id=body.encounter_id,
        language=analysis.primary_language,
        raw_transcript=analysis.raw_transcript,
        reference_dt=ref_dt,
        use_llm=body.use_llm,
    )
    safety = evaluate_safety(body.text, lang=analysis.primary_language, extraction=payload)
    return {
        "transcript": analysis.as_dict(),
        "extraction": payload.model_dump(),
        "safety": safety.as_dict(),
    }


class ConfirmRecordIn(BaseModel):
    entity_type: Literal["symptom", "medication", "condition", "allergy", "medical_history"]
    entity_id: str
    action: Literal["confirm", "correct", "resolve_discrepancy"] = "confirm"
    updates: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=200)


class PatchRecordIn(BaseModel):
    action: Literal["confirm", "correct", "resolve_discrepancy"] = "correct"
    updates: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=200)


@router.post("/records/confirm")
async def confirm_record_post(
    body: ConfirmRecordIn,
    cu: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await confirm_or_correct_record(
            db,
            cu,
            entity_type=body.entity_type,
            entity_id=body.entity_id,
            action=body.action,
            updates=body.updates,
            reason=body.reason,
        )
    except KeyError as e:
        raise HTTPException(404, str(e))


@router.patch("/records/{entity_type}/{entity_id}")
async def patch_record(
    entity_type: Literal["symptom", "medication", "condition", "allergy", "medical_history"],
    entity_id: str,
    body: PatchRecordIn,
    cu: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await confirm_or_correct_record(
            db,
            cu,
            entity_type=entity_type,
            entity_id=entity_id,
            action=body.action,
            updates=body.updates,
            reason=body.reason,
        )
    except KeyError as e:
        raise HTTPException(404, str(e))


class FollowupAnswerIn(BaseModel):
    answer_text: str = Field(min_length=1, max_length=2000)
    lang: str = "en"


@router.post("/followups/{qid}/answer")
async def answer_followup(
    qid: str,
    body: FollowupAnswerIn,
    cu: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    fq = await db.get(FollowUpQuestion, qid)
    if fq is None or fq.user_id != cu.id:
        raise HTTPException(404, "Follow-up question not found")

    analysis = analyze_transcript(body.answer_text, lang_hint=body.lang)
    payload = await extract_clinical_turn(
        analysis.normalized_transcript or body.answer_text,
        patient_id=cu.id,
        encounter_id=fq.encounter_id,
        language=body.lang,
        raw_transcript=analysis.raw_transcript,
        use_llm=False,
    )
    persist_summary = await persist_extraction(db, cu, payload, encounter_id=fq.encounter_id)
    completed = await record_followup_answer(
        db, cu, encounter_id=fq.encounter_id, answer_text=body.answer_text, extraction=payload
    )
    mem = await PatientContextBuilder().build(db, cu, query_text=body.answer_text, encounter_id=fq.encounter_id)
    safety = evaluate_safety(body.answer_text, lang=body.lang, extraction=payload)
    next_q = await next_followup_question(
        db, cu, encounter_id=fq.encounter_id, extraction=payload, mem=mem, safety=safety, lang=body.lang
    )
    return {
        "completed": completed,
        "persisted": persist_summary,
        "safety": safety.as_dict(),
        "next_followup": next_q,
    }


@router.get("/context")
async def patient_context_full(
    lang: str = Query(default="en"),
    encounter_id: str | None = Query(default=None),
    cu: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Dedicated Patient Context tab API (Part 9 sections A-M) with source transcript span links."""
    from app.models import ClinicalAssessment, ClinicalFact, ClinicalInvestigation, DifferentialConsideration, Transcript

    status_data = await clinical_status(lang=lang, cu=cu, db=db)

    tr_q = select(Transcript).where(Transcript.user_id == cu.id).order_by(Transcript.created_at.desc()).limit(25)
    transcripts = {
        tr.id: {
            "id": tr.id,
            "encounter_id": tr.encounter_id,
            "speaker": tr.speaker,
            "raw_text": _dec(cu, tr.raw_text_enc) or "",
            "corrected_text": _dec(cu, tr.corrected_text_enc) or "",
            "primary_language": tr.primary_language,
            "detected_languages": tr.detected_languages or [],
            "code_switched": tr.code_switched,
            "confidence": tr.confidence,
            "created_at": tr.created_at.isoformat() if tr.created_at else None,
        }
        for tr in await db.scalars(tr_q)
    }

    fact_rows = list(await db.scalars(
        select(ClinicalFact)
        .where(ClinicalFact.user_id == cu.id, ClinicalFact.active.is_(True))
        .order_by(ClinicalFact.observed_at.desc())
        .limit(120)
    ))
    facts = [
        {
            "id": f.id,
            "encounter_id": f.encounter_id,
            "transcript_id": f.transcript_id,
            "scope": f.scope,
            "category": f.category,
            "fact_key": f.fact_key,
            "value": _dec_json(cu, f.value_enc, {}),
            "original_quote": _dec(cu, f.original_quote_enc) or "",
            "span_start": f.span_start,
            "span_end": f.span_end,
            "speaker": f.speaker,
            "temporal_info": f.temporal_info,
            "verification_status": f.verification_status,
            "source_type": f.source_type,
            "confidence": f.confidence,
            "uncertainty_note": f.uncertainty_note,
            "is_contradiction": f.is_contradiction,
            "correction_history": f.correction_history or [],
            "observed_at": f.observed_at.isoformat() if f.observed_at else None,
        }
        for f in fact_rows
    ]

    latest_assess = await db.scalar(
        select(ClinicalAssessment)
        .where(ClinicalAssessment.user_id == cu.id)
        .order_by(ClinicalAssessment.created_at.desc())
        .limit(1)
    )
    diffs = list(await db.scalars(
        select(DifferentialConsideration)
        .where(DifferentialConsideration.user_id == cu.id, DifferentialConsideration.active.is_(True))
    ))
    inv_rows = list(await db.scalars(
        select(ClinicalInvestigation)
        .where(ClinicalInvestigation.user_id == cu.id)
        .order_by(ClinicalInvestigation.created_at.desc())
        .limit(12)
    ))
    vit_rows = list(await db.scalars(
        select(Vital).where(Vital.user_id == cu.id).order_by(Vital.measured_at.desc()).limit(25)
    ))
    events = list(await db.scalars(
        select(ClinicalEvent).where(ClinicalEvent.user_id == cu.id).order_by(desc(ClinicalEvent.occurred_at)).limit(40)
    ))

    return {
        "patient_id": cu.id,
        "disclaimer": disclaimer(lang),
        "returning_patient": status_data["returning_patient"],
        "prior_encounter_summary": status_data["prior_encounter_summary"],
        # Sections A-M
        "current_complaints": [s for s in status_data["active_symptoms"] if s["status"] != "resolved"],
        "symptom_details": status_data["active_symptoms"] + status_data["resolved_symptoms"],
        "medical_history": status_data["medical_history"] + status_data["conditions"],
        "medication_history": status_data["medications"],
        "allergies": status_data["allergies"],
        "measurements_and_vitals": [
            {
                "id": v.id, "type": v.type, "value": v.value, "unit": v.unit,
                "source": v.source, "is_estimate": bool(v.is_estimate),
                "original_statement": _dec(cu, v.original_statement_enc),
                "measured_at": v.measured_at.isoformat() if v.measured_at else None,
            }
            for v in vit_rows
        ],
        "functional_impact": [f for f in facts if f["category"] == "functional"] + [
            {"symptom": s["name"], "impact": s["functional_impact"]}
            for s in status_data["active_symptoms"] if s.get("functional_impact")
        ],
        "temporal_timeline": [
            {
                "id": ev.id, "event_type": ev.event_type, "entity_type": ev.entity_type,
                "source": ev.source, "verification_status": ev.verification_status,
                "discrepancy_flag": ev.discrepancy_flag,
                "payload": _dec_json(cu, ev.payload_enc, {}),
                "occurred_at": ev.occurred_at.isoformat() if ev.occurred_at else None,
            }
            for ev in events
        ],
        "unknown_information": [f for f in facts if f.get("uncertainty_note")] + (
            _dec_json(cu, latest_assess.missing_evidence_enc, []) if latest_assess else []
        ),
        "contradictions": status_data["discrepancies"] + [f for f in facts if f["is_contradiction"]],
        "clinical_assessment": (
            {
                "id": latest_assess.id,
                "version_no": latest_assess.version_no,
                "summary": _dec(cu, latest_assess.summary_enc),
                "established_facts": _dec_json(cu, latest_assess.established_facts_enc, {}),
                "missing_evidence": _dec_json(cu, latest_assess.missing_evidence_enc, []),
                "safety_status": latest_assess.safety_status,
                "interview_outcome": latest_assess.interview_outcome,
                "verification_status": latest_assess.verification_status,
                "differentials": [
                    {
                        "id": d.id,
                        "explanation": d.explanation,
                        "supporting_evidence": _dec_json(cu, d.supporting_evidence_enc, []),
                        "against_evidence": _dec_json(cu, d.against_evidence_enc, []),
                        "missing_evidence": _dec_json(cu, d.missing_evidence_enc, []),
                        "next_assessment": d.next_assessment,
                        "likelihood_band": d.likelihood_band,
                        "review_status": d.review_status,
                    }
                    for d in diffs
                ],
            }
            if latest_assess
            else None
        ),
        "pending_questions": status_data["pending_followups"],
        "recommended_next_steps": {
            "last_recommendation": status_data["last_recommendation"],
            "investigations": [
                {
                    "id": iv.id,
                    "name": iv.name,
                    "purpose_category": iv.purpose_category,
                    "urgency": iv.urgency,
                    "clinician_decision_required": iv.clinician_decision_required,
                    "rationale": _dec(cu, iv.rationale_enc) or "",
                    "supporting_findings": _dec_json(cu, iv.supporting_findings_enc, []),
                    "limitations": _dec(cu, iv.limitations_enc) or "",
                    "already_in_records": iv.already_in_records,
                    "matched_lab_id": iv.matched_lab_id,
                }
                for iv in inv_rows
            ],
        },
        "transcripts": transcripts,
        "facts": facts,
        "safety_alerts": status_data["safety_alerts"],
        "consultation_summary": {
            "patient_reported_information": {
                "chief_complaint": (status_data["active_symptoms"][0]["name"] if status_data["active_symptoms"] else None),
                "symptoms": [
                    {
                        "name": s.get("name"),
                        "location": s.get("body_part") or s.get("location"),
                        "duration": s.get("duration") or s.get("onset"),
                        "severity": s.get("severity"),
                        "character": s.get("character"),
                        "progression": s.get("progression"),
                        "triggers": s.get("triggers") or [],
                    }
                    for s in status_data["active_symptoms"]
                ],
                "medical_history": status_data["medical_history"] + status_data["conditions"],
                "current_medications": status_data["medications"],
                "allergies": status_data["allergies"],
                "important_negative_findings": [f for f in facts if f.get("fact_key") == "negated_symptom" or f.get("value", {}).get("negated")],
                "preferred_language": lang,
            },
            "ai_generated_clinical_observations": {
                "note": "AI-generated observations only — NOT a medical diagnosis. Confirm with a registered doctor.",
                "summary": _dec(cu, latest_assess.summary_enc) if latest_assess else "",
                "considerations": [
                    {
                        "explanation": d.explanation,
                        "likelihood_band": d.likelihood_band,
                        "supporting_evidence": _dec_json(cu, d.supporting_evidence_enc, []),
                        "against_evidence": _dec_json(cu, d.against_evidence_enc, []),
                    }
                    for d in diffs
                ],
                "red_flags": status_data["safety_alerts"],
            },
            "missing_information": (
                [q.get("reason") or q.get("question") for q in status_data["pending_followups"]]
                + (_dec_json(cu, latest_assess.missing_evidence_enc, []) if latest_assess else [])
            ),
            "next_steps": {
                "urgent_warning_signs": [
                    a.get("message") or a.get("title")
                    for a in status_data["safety_alerts"]
                    if isinstance(a, dict)
                ],
                "recommended_action": status_data["last_recommendation"] or "Consult a registered doctor for personalized evaluation.",
                "investigations_to_discuss": [
                    {
                        "name": iv.name,
                        "purpose": iv.purpose_category,
                        "rationale": _dec(cu, iv.rationale_enc) or "",
                        "limitations": _dec(cu, iv.limitations_enc) or "",
                    }
                    for iv in inv_rows
                ],
            },
        },
    }


@router.get("/consultation-summary")
async def consultation_summary(
    lang: str = Query(default="en"),
    encounter_id: str | None = Query(default=None),
    cu: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Structured 4-section clinical Consultation Summary separating patient facts from AI observations."""
    ctx = await patient_context_full(lang=lang, encounter_id=encounter_id, cu=cu, db=db)
    return {
        "patient_id": ctx["patient_id"],
        "disclaimer": ctx["disclaimer"],
        "consultation_summary": ctx["consultation_summary"],
    }


class CdssEvaluateIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    lang: str = "en"
    age_years: int | None = None
    pregnant: bool = False
    comorbidities: list[str] = Field(default_factory=list)


@router.post("/cdss-evaluate")
async def cdss_evaluate(body: CdssEvaluateIn, cu: CurrentUser = Depends(current_user)):
    """Run specialty classification, MBBS/MD/MS/DM/MCh routing, investigation selection & 9-section CDSS output."""
    from app.clinical.cdss import build_nine_sections, evaluate_cdss_context
    from app.safety.red_flags import check as rf_check

    rf = rf_check(body.text, lang=body.lang, age_years=body.age_years)
    cdss = evaluate_cdss_context(
        body.text,
        lang=body.lang,
        age_years=body.age_years,
        pregnant=body.pregnant,
        comorbidities=body.comorbidities,
        red_flag_triggered=rf.triggered,
        red_flag_kind=rf.kind,
    )
    sections = build_nine_sections(
        text=body.text,
        lang=body.lang,
        structured={"reply": body.text, "possible_conditions": [], "watch_for": rf.messages if rf.triggered else []},
        cdss=cdss,
    )
    return {
        "patient_id": cu.id,
        "red_flag": rf.as_dict(),
        "routing": cdss["routing"].as_dict(),
        "investigations": cdss["investigations"],
        "clinical_sections": sections,
    }


class FactCorrectionIn(BaseModel):
    value: dict[str, Any]
    reason: str = Field(default="", max_length=200)
    actor_role: Literal["patient", "clinician"] = "patient"
    client_updated_at: datetime | None = None


@router.post("/facts/{fid}/correct")
async def correct_fact_endpoint(
    fid: str,
    body: FactCorrectionIn,
    cu: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.clinical.facts import correct_clinical_fact

    try:
        return await correct_clinical_fact(
            db,
            cu,
            fact_id=fid,
            new_value=body.value,
            reason=body.reason,
            actor_role=body.actor_role,
            client_updated_at=body.client_updated_at,
        )
    except KeyError as e:
        raise HTTPException(404, str(e))
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(409, str(e))


class LiveGreetingIn(BaseModel):
    lang: str = "en"
    session_id: str | None = None


@router.post("/live-greeting")
async def live_greeting(
    body: LiveGreetingIn,
    cu: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the exact initial Live Conversation greeting and patient identity/confirmation state."""
    from app.services.language_consistency import LanguageConsistencyService
    from app.services.patient_profile import PatientProfileService

    lang = (body.lang or cu.user.preferred_lang or "en").split("-")[0].lower()
    if body.session_id:
        LanguageConsistencyService().set_locked(body.session_id, lang)
    svc = PatientProfileService()
    snap = await svc.load_snapshot(db, cu, session_id=body.session_id)
    onboarding = svc.onboarding_prompt(snap, lang=lang)
    return {
        "language": lang,
        "greeting": onboarding["greeting"],
        "prompt": onboarding["prompt"],
        "missing_fields": onboarding["missing_fields"],
        "status": onboarding["status"],
        "patient": onboarding["patient"],
        "ready_prompt": svc.ready_prompt_for(lang),
    }


@router.get("/sessions/{sid}/compact-summary")
async def get_compact_summary(
    sid: str,
    lang: str = Query(default="en"),
    cu: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the automatic compact structured consultation summary for session `sid`."""
    from app.models import ChatSession
    from app.services.patient_summary import PatientSummaryService

    sess = await db.get(ChatSession, sid)
    if sess is None or sess.user_id != cu.id:
        raise HTTPException(404, "Session not found")
    svc = PatientSummaryService()
    summary = await svc.build_compact_summary(db, cu, session_id=sid, lang=lang or sess.lang)
    return {"session_id": sid, "compact_summary": summary}


class CompactSummaryPatchIn(BaseModel):
    patient_name: str | None = Field(None, max_length=120)
    age: int | None = Field(None, ge=0, le=125)
    preferred_language: str | None = Field(None, max_length=12)
    lang: str = "en"


@router.patch("/sessions/{sid}/compact-summary")
async def patch_compact_summary(
    sid: str,
    body: CompactSummaryPatchIn,
    cu: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Allow the patient to correct their name, age, or preferred language and regenerate the summary."""
    from app.models import ChatSession, PatientProfile
    from app.security.consent import active_purposes
    from app.services.language_consistency import LanguageConsistencyService
    from app.services.patient_summary import PatientSummaryService

    sess = await db.get(ChatSession, sid)
    if sess is None or sess.user_id != cu.id:
        raise HTTPException(404, "Session not found")
    purposes = await active_purposes(db, cu.account_id)
    if "core_service" in purposes:
        p = await db.get(PatientProfile, cu.id) or PatientProfile(user_id=cu.id)
        if body.patient_name is not None:
            clean_name = body.patient_name.strip()
            p.name_enc = cu.cipher.enc(clean_name) if clean_name else None
        if body.age is not None:
            p.age_years = body.age
            p.dob_enc = None
        if body.preferred_language is not None:
            clean_lang = body.preferred_language.split("-")[0].lower()
            cu.user.preferred_lang = clean_lang
            sess.lang = clean_lang
            LanguageConsistencyService().set_locked(sid, clean_lang)
        db.add(p)
        await db.commit()

    svc = PatientSummaryService()
    use_lang = (body.preferred_language or body.lang or sess.lang or "en").split("-")[0].lower()
    summary = await svc.build_compact_summary(db, cu, session_id=sid, lang=use_lang)
    await svc.save_if_consented(db, cu, session_id=sid, summary=summary, lang=use_lang)
    return {"session_id": sid, "compact_summary": summary}


@router.delete("/sessions/{sid}/compact-summary", status_code=204)
async def delete_compact_summary(
    sid: str,
    cu: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete the saved consultation summary for session `sid` (privacy erasure)."""
    enc = (await db.scalars(
        select(Encounter).where(Encounter.user_id == cu.id, Encounter.chat_session_id == sid)
    )).first()
    if enc is not None:
        enc.summary_enc = None
        await db.commit()
