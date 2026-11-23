"""Validated clinical persistence layer, longitudinal symptom state machine, and auditable corrections.

All writes from clinical extraction pass through this module. Language models never write to the DB directly.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Literal

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clinical.schema import ClinicalExtractionPayload, SymptomExtracted
from app.models import (
    Allergy,
    AuditLog,
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
    PatientProfile,
    SafetyAlert,
    SymptomObservation,
    Vital,
)
from app.security.auth import CurrentUser

log = logging.getLogger("sushrut.clinical.persistence")

EVENT_TYPES = frozenset({
    "SYMPTOM_REPORTED",
    "SYMPTOM_STARTED",
    "SYMPTOM_WORSENED",
    "SYMPTOM_IMPROVED",
    "SYMPTOM_RESOLVED",
    "MEDICATION_REPORTED",
    "MEDICATION_STARTED",
    "MEDICATION_STOPPED",
    "ALLERGY_REPORTED",
    "VITAL_RECORDED",
    "DIAGNOSIS_DOCUMENTED",
    "CLINICAL_RECOMMENDATION",
    "FOLLOWUP_COMPLETED",
    "EMERGENCY_ESCALATION",
    "RECORD_CORRECTED",
})


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _enc_json(cu: CurrentUser, obj) -> bytes:
    return cu.cipher.enc(json.dumps(obj, ensure_ascii=False, default=str))


def _dec_json(cu: CurrentUser, blob: bytes | None, default=None):
    if not blob:
        return default
    try:
        return json.loads(cu.cipher.dec(blob))
    except Exception:  # noqa: BLE001
        return default


def _parse_iso_dt(val: str | None) -> datetime | None:
    if not val:
        return None
    try:
        s = val.rstrip("Z")
        return datetime.fromisoformat(s).replace(tzinfo=None)
    except ValueError:
        return None


async def emit_event(
    db: AsyncSession,
    cu: CurrentUser,
    *,
    encounter_id: str | None,
    event_type: str,
    entity_type: str,
    entity_id: str,
    source_turn_id: str | None = None,
    source: str = "patient_report",
    verification_status: str = "patient_reported",
    discrepancy_flag: bool = False,
    payload: dict | None = None,
    occurred_at: datetime | None = None,
) -> ClinicalEvent:
    ev = ClinicalEvent(
        user_id=cu.id,
        encounter_id=encounter_id,
        event_type=event_type if event_type in EVENT_TYPES else "CLINICAL_RECOMMENDATION",
        entity_type=entity_type,
        entity_id=entity_id,
        source_turn_id=source_turn_id,
        source=source,
        verification_status=verification_status,
        discrepancy_flag=discrepancy_flag,
        payload_enc=_enc_json(cu, payload or {}),
        occurred_at=occurred_at or _now(),
    )
    db.add(ev)
    await db.flush()
    return ev


async def get_or_create_encounter(
    db: AsyncSession,
    cu: CurrentUser,
    *,
    chat_session_id: str | None = None,
    channel: str = "text",
    language: str = "en",
    chief_complaint: str | None = None,
) -> Encounter:
    if chat_session_id:
        existing = await db.scalar(
            select(Encounter)
            .where(Encounter.user_id == cu.id, Encounter.chat_session_id == chat_session_id)
            .order_by(Encounter.started_at.desc())
            .limit(1)
        )
        if existing is not None:
            if chief_complaint and not existing.chief_complaint_enc:
                existing.chief_complaint_enc = cu.cipher.enc(chief_complaint[:300])
            if channel == "voice" and existing.channel != "voice":
                existing.channel = "voice"
            return existing

    enc = Encounter(
        user_id=cu.id,
        chat_session_id=chat_session_id,
        status="active",
        channel=channel,
        language=language,
        chief_complaint_enc=cu.cipher.enc(chief_complaint[:300]) if chief_complaint else None,
        started_at=_now(),
    )
    db.add(enc)
    await db.flush()
    return enc


async def record_turn(
    db: AsyncSession,
    cu: CurrentUser,
    *,
    encounter_id: str,
    session_id: str | None = None,
    role: str = "user",
    raw_text: str,
    normalized_text: str | None = None,
    detected_lang: str = "en",
    code_switched: bool = False,
    asr_confidence: float | None = None,
    asr_engine: str | None = None,
    speech_segments: list | None = None,
    extracted_payload: dict | None = None,
) -> ConversationTurn:
    prior_count = len(list(await db.scalars(
        select(ConversationTurn.id).where(ConversationTurn.encounter_id == encounter_id)
    )))
    turn = ConversationTurn(
        user_id=cu.id,
        encounter_id=encounter_id,
        session_id=session_id,
        turn_index=prior_count + 1,
        role=role,
        raw_transcript_enc=cu.cipher.enc(raw_text),
        normalized_transcript_enc=cu.cipher.enc(normalized_text) if normalized_text is not None else None,
        detected_lang=detected_lang[:16],
        code_switched=code_switched,
        asr_confidence=asr_confidence,
        asr_engine=asr_engine[:64] if asr_engine else None,
        speech_segments=speech_segments,
        extracted_payload_enc=_enc_json(cu, extracted_payload) if extracted_payload else None,
    )
    db.add(turn)
    await db.flush()
    return turn


def _match_symptom(name: str, existing: list[ClinicalSymptom]) -> ClinicalSymptom | None:
    target = name.lower().strip()
    for sx in existing:
        if sx.name.lower() == target:
            return sx
    best = max(existing, key=lambda s: fuzz.token_set_ratio(target, s.name.lower()), default=None)
    if best and fuzz.token_set_ratio(target, best.name.lower()) >= 85:
        return best
    return None


def _detect_symptom_discrepancy(existing: ClinicalSymptom, new_sx: SymptomExtracted) -> str | None:
    """Detect contradictory statements without silently overwriting the earlier record."""
    if new_sx.present is False and existing.status == "active" and new_sx.progression != "resolved":
        # Mentioned as active earlier in the same window/encounter, now explicitly denied without saying it resolved
        return f"Earlier reported '{existing.name}' as {existing.status}, but newly stated absent without noting resolution."
    if (
        existing.estimated_onset
        and new_sx.temporal.estimated_onset
        and (new_dt := _parse_iso_dt(new_sx.temporal.estimated_onset)) is not None
    ):
        diff_days = abs((existing.estimated_onset.date() - new_dt.date()).days)
        if diff_days >= 3:
            return (
                f"Onset mismatch for '{existing.name}': previously estimated around "
                f"{existing.estimated_onset.date().isoformat()}, now reported as '{new_sx.onset or new_sx.duration}' "
                f"(~{new_dt.date().isoformat()})."
            )
    if (
        existing.severity in ("mild", "severe")
        and new_sx.severity in ("mild", "severe")
        and existing.severity != new_sx.severity
        and new_sx.progression == "unknown"
        and (existing.last_reported_at and (_now() - existing.last_reported_at) < timedelta(hours=6))
    ):
        return f"Severity changed quickly for '{existing.name}' ({existing.severity} vs {new_sx.severity}) without progression context."
    return None


async def _upsert_memory_item(
    db: AsyncSession,
    cu: CurrentUser,
    *,
    memory_layer: str,
    fact_type: str,
    topic_key: str,
    content: dict,
    verification_status: str = "patient_reported",
    source: str = "patient_report",
    source_ref: str | None = None,
    confidence: float = 0.85,
) -> PatientMemoryItem:
    item = PatientMemoryItem(
        user_id=cu.id,
        memory_layer=memory_layer,
        fact_type=fact_type,
        topic_key=topic_key[:120],
        content_enc=_enc_json(cu, content),
        verification_status=verification_status,
        source=source,
        source_ref=source_ref,
        confidence=confidence,
        active=True,
    )
    db.add(item)
    await db.flush()
    return item


async def persist_extraction(
    db: AsyncSession,
    cu: CurrentUser,
    payload: ClinicalExtractionPayload,
    *,
    encounter_id: str,
    turn_id: str | None = None,
) -> dict:
    """Persist validated extraction payload into longitudinal clinical tables inside a nested transaction."""
    now = _now()
    summary = {
        "encounter_id": encounter_id,
        "symptoms_created": [],
        "symptoms_updated": [],
        "discrepancies": [],
        "medications_added": [],
        "unidentified_medications": [],
        "vitals_recorded": [],
        "events_emitted": [],
    }

    async with db.begin_nested():
        existing_symptoms = list(
            await db.scalars(select(ClinicalSymptom).where(ClinicalSymptom.user_id == cu.id))
        )

        # 1. Active / reported symptoms
        for sx in payload.symptoms:
            match = _match_symptom(sx.name, existing_symptoms)
            est_onset = _parse_iso_dt(sx.temporal.estimated_onset)
            if match is None:
                rec = ClinicalSymptom(
                    user_id=cu.id,
                    encounter_id=encounter_id,
                    name=sx.name[:120],
                    normalized_concept=sx.normalized_description[:160],
                    anatomical_location=sx.anatomical_location,
                    status=sx.status if sx.status != "unknown" else "active",
                    progression=sx.progression if sx.progression != "unknown" else "new",
                    onset_text=sx.onset,
                    estimated_onset=est_onset,
                    onset_approximate=sx.temporal.is_approximate or sx.temporal.is_temporal_uncertain,
                    duration_text=sx.duration,
                    frequency=sx.frequency,
                    severity=sx.severity,
                    character=sx.character,
                    functional_impact_enc=cu.cipher.enc(sx.functional_impact) if sx.functional_impact else None,
                    triggers_enc=_enc_json(cu, sx.triggers) if sx.triggers else None,
                    relieving_factors_enc=_enc_json(cu, sx.relieving_factors) if sx.relieving_factors else None,
                    associated_symptoms_enc=_enc_json(cu, sx.associated_symptoms) if sx.associated_symptoms else None,
                    original_statement_enc=cu.cipher.enc(sx.original_statement),
                    confidence=sx.confidence,
                    verification_status=sx.verification_status,
                    source=sx.source,
                    first_reported_at=est_onset or now,
                    last_reported_at=now,
                    resolved_at=now if sx.status == "resolved" else None,
                )
                db.add(rec)
                await db.flush()
                existing_symptoms.append(rec)

                obs = SymptomObservation(
                    user_id=cu.id,
                    symptom_id=rec.id,
                    encounter_id=encounter_id,
                    turn_id=turn_id,
                    observed_at=now,
                    reported_status=rec.status,
                    progression=rec.progression,
                    severity=sx.severity,
                    duration_text=sx.duration,
                    frequency=sx.frequency,
                    triggers_enc=_enc_json(cu, sx.triggers) if sx.triggers else None,
                    relieving_factors_enc=_enc_json(cu, sx.relieving_factors) if sx.relieving_factors else None,
                    functional_impact_enc=cu.cipher.enc(sx.functional_impact) if sx.functional_impact else None,
                    original_statement_enc=cu.cipher.enc(sx.original_statement),
                    confidence=sx.confidence,
                    verification_status=sx.verification_status,
                    source=sx.source,
                )
                db.add(obs)
                await emit_event(
                    db,
                    cu,
                    encounter_id=encounter_id,
                    event_type="SYMPTOM_REPORTED",
                    entity_type="symptom",
                    entity_id=rec.id,
                    source_turn_id=turn_id,
                    source=sx.source,
                    verification_status=sx.verification_status,
                    payload={"name": rec.name, "severity": rec.severity, "duration": rec.duration_text, "triggers": sx.triggers},
                    occurred_at=now,
                )
                summary["events_emitted"].append("SYMPTOM_REPORTED")
                if est_onset or sx.onset:
                    await emit_event(
                        db,
                        cu,
                        encounter_id=encounter_id,
                        event_type="SYMPTOM_STARTED",
                        entity_type="symptom",
                        entity_id=rec.id,
                        source_turn_id=turn_id,
                        source=sx.source,
                        verification_status=sx.verification_status,
                        payload={"name": rec.name, "onset_text": sx.onset, "estimated_onset": sx.temporal.estimated_onset, "approximate": rec.onset_approximate},
                        occurred_at=est_onset or now,
                    )
                    summary["events_emitted"].append("SYMPTOM_STARTED")
                await _upsert_memory_item(
                    db,
                    cu,
                    memory_layer="short_term",
                    fact_type="symptom",
                    topic_key=rec.name,
                    content={"symptom_id": rec.id, "name": rec.name, "status": rec.status, "duration": rec.duration_text, "triggers": sx.triggers, "functional_impact": sx.functional_impact},
                    verification_status=sx.verification_status,
                    source=sx.source,
                    source_ref=encounter_id,
                    confidence=sx.confidence,
                )
                summary["symptoms_created"].append(rec.name)
            else:
                discrepancy = _detect_symptom_discrepancy(match, sx)
                # Preserve old observations; append new observation
                obs = SymptomObservation(
                    user_id=cu.id,
                    symptom_id=match.id,
                    encounter_id=encounter_id,
                    turn_id=turn_id,
                    observed_at=now,
                    reported_status=sx.status if sx.status != "unknown" else match.status,
                    progression=sx.progression,
                    severity=sx.severity if sx.severity != "unknown" else match.severity,
                    duration_text=sx.duration,
                    frequency=sx.frequency,
                    triggers_enc=_enc_json(cu, sx.triggers) if sx.triggers else None,
                    relieving_factors_enc=_enc_json(cu, sx.relieving_factors) if sx.relieving_factors else None,
                    functional_impact_enc=cu.cipher.enc(sx.functional_impact) if sx.functional_impact else None,
                    original_statement_enc=cu.cipher.enc(sx.original_statement),
                    discrepancy_note_enc=cu.cipher.enc(discrepancy) if discrepancy else None,
                    is_contradictory=bool(discrepancy),
                    confidence=sx.confidence,
                    verification_status=sx.verification_status,
                    source=sx.source,
                )
                db.add(obs)

                prev_status = match.status
                if sx.progression == "resolved" or sx.status == "resolved":
                    match.status = "resolved"
                    match.progression = "resolved"
                    match.resolved_at = now
                    ev_type = "SYMPTOM_RESOLVED"
                elif sx.progression == "worsening":
                    match.status = "worsening"
                    match.progression = "worsening"
                    ev_type = "SYMPTOM_WORSENED"
                elif sx.progression == "improving":
                    match.status = "improving"
                    match.progression = "improving"
                    ev_type = "SYMPTOM_IMPROVED"
                else:
                    if prev_status == "resolved":
                        match.status = "active"
                        match.progression = "recurrent"
                        match.resolved_at = None
                    else:
                        match.status = "active"
                    ev_type = "SYMPTOM_REPORTED"

                match.last_reported_at = now
                match.encounter_id = encounter_id
                if sx.severity != "unknown":
                    match.severity = sx.severity
                if sx.duration and not discrepancy:
                    match.duration_text = sx.duration
                if sx.frequency:
                    match.frequency = sx.frequency
                if sx.functional_impact:
                    match.functional_impact_enc = cu.cipher.enc(sx.functional_impact)
                if sx.triggers:
                    old_tr = _dec_json(cu, match.triggers_enc, [])
                    merged_tr = list(dict.fromkeys([*old_tr, *sx.triggers]))
                    match.triggers_enc = _enc_json(cu, merged_tr)
                if sx.relieving_factors:
                    old_rl = _dec_json(cu, match.relieving_factors_enc, [])
                    match.relieving_factors_enc = _enc_json(cu, list(dict.fromkeys([*old_rl, *sx.relieving_factors])))

                await emit_event(
                    db,
                    cu,
                    encounter_id=encounter_id,
                    event_type=ev_type,
                    entity_type="symptom",
                    entity_id=match.id,
                    source_turn_id=turn_id,
                    source=sx.source,
                    verification_status=sx.verification_status,
                    discrepancy_flag=bool(discrepancy),
                    payload={"name": match.name, "status": match.status, "progression": match.progression, "discrepancy": discrepancy, "statement": sx.original_statement},
                    occurred_at=now,
                )
                summary["events_emitted"].append(ev_type)
                summary["symptoms_updated"].append(match.name)
                if discrepancy:
                    summary["discrepancies"].append(discrepancy)
                    await _upsert_memory_item(
                        db,
                        cu,
                        memory_layer="short_term",
                        fact_type="discrepancy",
                        topic_key=f"discrepancy:{match.name}",
                        content={"symptom_id": match.id, "symptom": match.name, "note": discrepancy},
                        verification_status="unverified",
                        source="system_audit",
                        source_ref=encounter_id,
                    )

        # 2. Explicitly negated or resolved symptoms
        for neg in payload.negated_symptoms:
            match = _match_symptom(neg.name, existing_symptoms)
            if match is not None:
                discrepancy = _detect_symptom_discrepancy(match, neg)
                obs = SymptomObservation(
                    user_id=cu.id,
                    symptom_id=match.id,
                    encounter_id=encounter_id,
                    turn_id=turn_id,
                    observed_at=now,
                    reported_status="resolved" if neg.progression == "resolved" else match.status,
                    progression=neg.progression,
                    severity="not_assessed",
                    original_statement_enc=cu.cipher.enc(neg.original_statement),
                    discrepancy_note_enc=cu.cipher.enc(discrepancy) if discrepancy else None,
                    is_contradictory=bool(discrepancy),
                    confidence=neg.confidence,
                    verification_status=neg.verification_status,
                    source=neg.source,
                )
                db.add(obs)
                if neg.progression == "resolved":
                    match.status = "resolved"
                    match.progression = "resolved"
                    match.resolved_at = now
                    await emit_event(
                        db,
                        cu,
                        encounter_id=encounter_id,
                        event_type="SYMPTOM_RESOLVED",
                        entity_type="symptom",
                        entity_id=match.id,
                        source_turn_id=turn_id,
                        source=neg.source,
                        verification_status=neg.verification_status,
                        payload={"name": match.name, "statement": neg.original_statement},
                        occurred_at=now,
                    )
                    summary["events_emitted"].append("SYMPTOM_RESOLVED")
                elif discrepancy:
                    summary["discrepancies"].append(discrepancy)
                    await emit_event(
                        db,
                        cu,
                        encounter_id=encounter_id,
                        event_type="SYMPTOM_REPORTED",
                        entity_type="symptom",
                        entity_id=match.id,
                        source_turn_id=turn_id,
                        source=neg.source,
                        verification_status=neg.verification_status,
                        discrepancy_flag=True,
                        payload={"name": match.name, "present": False, "discrepancy": discrepancy},
                        occurred_at=now,
                    )
                    await _upsert_memory_item(
                        db,
                        cu,
                        memory_layer="short_term",
                        fact_type="discrepancy",
                        topic_key=f"discrepancy:{match.name}",
                        content={"symptom_id": match.id, "symptom": match.name, "note": discrepancy},
                        verification_status="unverified",
                        source="system_audit",
                        source_ref=encounter_id,
                    )
            else:
                await _upsert_memory_item(
                    db,
                    cu,
                    memory_layer="short_term",
                    fact_type="symptom",
                    topic_key=f"neg:{neg.name}",
                    content={"name": neg.name, "present": False, "statement": neg.original_statement},
                    verification_status=neg.verification_status,
                    source=neg.source,
                    source_ref=encounter_id,
                    confidence=neg.confidence,
                )

        # 3. Medications (named vs unidentified)
        existing_meds = list(await db.scalars(select(Medication).where(Medication.user_id == cu.id)))
        for md in payload.medications:
            if md.unidentified:
                await _upsert_memory_item(
                    db,
                    cu,
                    memory_layer="short_term",
                    fact_type="medication",
                    topic_key="unidentified_medicine",
                    content={"reported": md.medication_name_reported, "needs_photo_or_pharmacist": True, "statement": md.original_statement},
                    verification_status="unverified",
                    source=md.source,
                    source_ref=encounter_id,
                )
                await emit_event(
                    db,
                    cu,
                    encounter_id=encounter_id,
                    event_type="MEDICATION_REPORTED",
                    entity_type="medication",
                    entity_id="unidentified",
                    source_turn_id=turn_id,
                    source=md.source,
                    verification_status="unverified",
                    payload={"reported": md.medication_name_reported, "unidentified": True, "needs_photo_or_pharmacist": True},
                    occurred_at=now,
                )
                summary["unidentified_medications"].append(md.medication_name_reported)
                summary["events_emitted"].append("MEDICATION_REPORTED")
                continue

            mname = md.normalized_medication_name or md.medication_name_reported
            existing_m = next((m for m in existing_meds if (cu.cipher.dec(m.name_enc) or "").lower() == mname.lower()), None)
            if existing_m is None:
                m_row = Medication(
                    user_id=cu.id,
                    name_enc=cu.cipher.enc(mname),
                    dose=md.dosage,
                    route=md.route,
                    status="stopped" if md.stop_date else "active",
                    reason_enc=cu.cipher.enc(md.reason_for_use) if md.reason_for_use else None,
                    reported_effectiveness=md.reported_effectiveness if md.reported_effectiveness != "not_assessed" else None,
                    adverse_effects_enc=_enc_json(cu, md.adverse_effects) if md.adverse_effects else None,
                    prescribed_or_self=md.prescribed_or_self,
                    verification_status=md.verification_status,
                    source=md.source,
                )
                db.add(m_row)
                await db.flush()
                existing_meds.append(m_row)
                ev_t = "MEDICATION_STOPPED" if md.stop_date else "MEDICATION_STARTED"
                await emit_event(
                    db,
                    cu,
                    encounter_id=encounter_id,
                    event_type=ev_t,
                    entity_type="medication",
                    entity_id=m_row.id,
                    source_turn_id=turn_id,
                    source=md.source,
                    verification_status=md.verification_status,
                    payload={"name": mname, "prescribed_or_self": md.prescribed_or_self, "effectiveness": md.reported_effectiveness},
                    occurred_at=now,
                )
                summary["medications_added"].append(mname)
                summary["events_emitted"].append(ev_t)
            else:
                if md.reported_effectiveness != "not_assessed":
                    existing_m.reported_effectiveness = md.reported_effectiveness
                if md.stop_date:
                    existing_m.status = "stopped"
                    await emit_event(
                        db,
                        cu,
                        encounter_id=encounter_id,
                        event_type="MEDICATION_STOPPED",
                        entity_type="medication",
                        entity_id=existing_m.id,
                        source_turn_id=turn_id,
                        source=md.source,
                        verification_status=md.verification_status,
                        payload={"name": mname},
                        occurred_at=now,
                    )
                    summary["events_emitted"].append("MEDICATION_STOPPED")
                else:
                    await emit_event(
                        db,
                        cu,
                        encounter_id=encounter_id,
                        event_type="MEDICATION_REPORTED",
                        entity_type="medication",
                        entity_id=existing_m.id,
                        source_turn_id=turn_id,
                        source=md.source,
                        verification_status=md.verification_status,
                        payload={"name": mname, "effectiveness": md.reported_effectiveness},
                        occurred_at=now,
                    )
                    summary["events_emitted"].append("MEDICATION_REPORTED")

        # 4. Allergies
        existing_alls = {
            (cu.cipher.dec(a.substance_enc) or "").lower()
            for a in await db.scalars(select(Allergy).where(Allergy.user_id == cu.id))
        }
        for al in payload.allergies:
            if al.substance.lower() not in existing_alls:
                a_row = Allergy(
                    user_id=cu.id,
                    substance_enc=cu.cipher.enc(al.substance),
                    type=al.allergy_type if al.allergy_type != "unknown" else "other",
                    reaction_enc=cu.cipher.enc(al.reaction) if al.reaction else None,
                    severity=al.severity if al.severity != "unknown" else None,
                    verification_status=al.verification_status,
                    source=al.source,
                )
                db.add(a_row)
                await db.flush()
                existing_alls.add(al.substance.lower())
                await emit_event(
                    db,
                    cu,
                    encounter_id=encounter_id,
                    event_type="ALLERGY_REPORTED",
                    entity_type="allergy",
                    entity_id=a_row.id,
                    source_turn_id=turn_id,
                    source=al.source,
                    verification_status=al.verification_status,
                    payload={"substance": al.substance},
                    occurred_at=now,
                )
                summary["events_emitted"].append("ALLERGY_REPORTED")

        # 5. Medical history (chronic conditions, surgeries, hospitalizations, evaluations)
        existing_hist = {
            (h.category, (h.normalized_name or "").lower())
            for h in await db.scalars(select(MedicalHistoryEntry).where(MedicalHistoryEntry.user_id == cu.id))
        }
        for mh in payload.medical_history:
            key = (mh.category, (mh.normalized_name or mh.description).lower())
            if key not in existing_hist:
                h_row = MedicalHistoryEntry(
                    user_id=cu.id,
                    category=mh.category,
                    normalized_name=mh.normalized_name or mh.description.lower(),
                    description_enc=cu.cipher.enc(mh.description),
                    status=mh.status,
                    onset_or_date_text=mh.date_or_period,
                    verification_status=mh.verification_status,
                    source=mh.source,
                    source_ref=encounter_id,
                    recorded_at=now,
                )
                db.add(h_row)
                await db.flush()
                existing_hist.add(key)
                await _upsert_memory_item(
                    db,
                    cu,
                    memory_layer="long_term",
                    fact_type="history",
                    topic_key=f"{mh.category}:{h_row.normalized_name}",
                    content={"category": mh.category, "description": mh.description, "status": mh.status},
                    verification_status=mh.verification_status,
                    source=mh.source,
                    source_ref=encounter_id,
                )
                if mh.category in ("diagnosis", "chronic_condition"):
                    await emit_event(
                        db,
                        cu,
                        encounter_id=encounter_id,
                        event_type="DIAGNOSIS_DOCUMENTED",
                        entity_type="medical_history",
                        entity_id=h_row.id,
                        source_turn_id=turn_id,
                        source=mh.source,
                        verification_status=mh.verification_status,
                        payload={"category": mh.category, "description": mh.description, "status": mh.status},
                        occurred_at=now,
                    )
                    summary["events_emitted"].append("DIAGNOSIS_DOCUMENTED")

        # 6. Vital measurements (distinguishing measured vs estimate)
        for vm in payload.vital_measurements:
            v_row = Vital(
                user_id=cu.id,
                type=vm.vital_type,
                value=vm.value,
                unit=vm.unit,
                measured_at=_parse_iso_dt(vm.measured_at) or now,
                source=vm.source[:16],
                is_estimate=vm.is_estimate,
                original_statement_enc=cu.cipher.enc(vm.original_statement),
            )
            db.add(v_row)
            await db.flush()
            await emit_event(
                db,
                cu,
                encounter_id=encounter_id,
                event_type="VITAL_RECORDED",
                entity_type="vital",
                entity_id=v_row.id,
                source_turn_id=turn_id,
                source=vm.source,
                verification_status="unverified" if vm.is_estimate else "patient_reported",
                payload={"type": vm.vital_type, "value": vm.value, "unit": vm.unit, "is_estimate": vm.is_estimate},
                occurred_at=v_row.measured_at,
            )
            summary["vitals_recorded"].append(f"{vm.vital_type}={vm.value}{vm.unit}")
            summary["events_emitted"].append("VITAL_RECORDED")

        # 7. Volunteered social/functional context -> profile (ONLY if empty/volunteered, never invented)
        if payload.functional_context.occupation or payload.functional_context.hydration or payload.functional_context.sleep:
            prof = await db.get(PatientProfile, cu.id) or PatientProfile(user_id=cu.id)
            if payload.functional_context.occupation and not prof.occupation_enc:
                prof.occupation_enc = cu.cipher.enc(payload.functional_context.occupation[:80])
            if payload.functional_context.hydration:
                prof.hydration_enc = cu.cipher.enc(payload.functional_context.hydration[:80])
            if payload.functional_context.sleep:
                prof.sleep_enc = cu.cipher.enc(payload.functional_context.sleep[:80])
            db.add(prof)

    await db.commit()
    return summary


async def record_safety_alert(
    db: AsyncSession,
    cu: CurrentUser,
    *,
    encounter_id: str | None,
    turn_id: str | None,
    severity: str,
    rule_ids: list[str],
    guidance: str,
    call_numbers: list[str],
) -> SafetyAlert:
    alert = SafetyAlert(
        user_id=cu.id,
        encounter_id=encounter_id,
        turn_id=turn_id,
        severity=severity,
        rule_ids=rule_ids,
        guidance_enc=cu.cipher.enc(guidance),
        call_numbers=call_numbers,
        triggered_at=_now(),
    )
    db.add(alert)
    await db.flush()
    await emit_event(
        db,
        cu,
        encounter_id=encounter_id,
        event_type="EMERGENCY_ESCALATION",
        entity_type="safety_alert",
        entity_id=alert.id,
        source_turn_id=turn_id,
        source="safety_engine",
        verification_status="clinician_confirmed" if severity == "emergency" else "patient_reported",
        payload={"severity": severity, "rule_ids": rule_ids, "call_numbers": call_numbers},
        occurred_at=alert.triggered_at,
    )
    await db.commit()
    return alert


async def record_recommendation(
    db: AsyncSession,
    cu: CurrentUser,
    *,
    encounter_id: str,
    urgency: str,
    specialist: str | None,
    summary_text: str,
    self_care: list[str] | None = None,
    watch_for: list[str] | None = None,
) -> ClinicalRecommendation:
    rec = ClinicalRecommendation(
        user_id=cu.id,
        encounter_id=encounter_id,
        urgency=urgency,
        specialist=specialist,
        summary_enc=cu.cipher.enc(summary_text[:2000]),
        self_care_enc=_enc_json(cu, self_care) if self_care else None,
        watch_for_enc=_enc_json(cu, watch_for) if watch_for else None,
        verification_status="ai_hypothesis",
        source="assistant",
    )
    db.add(rec)
    await db.flush()
    enc = await db.get(Encounter, encounter_id)
    if enc is not None:
        enc.urgency = urgency
        enc.specialist = specialist
        enc.summary_enc = cu.cipher.enc(summary_text[:500])
    await emit_event(
        db,
        cu,
        encounter_id=encounter_id,
        event_type="CLINICAL_RECOMMENDATION",
        entity_type="clinical_recommendation",
        entity_id=rec.id,
        source="assistant",
        verification_status="ai_hypothesis",
        payload={"urgency": urgency, "specialist": specialist},
    )
    await db.commit()
    return rec


async def confirm_or_correct_record(
    db: AsyncSession,
    cu: CurrentUser,
    *,
    entity_type: Literal["symptom", "medication", "condition", "allergy", "medical_history"],
    entity_id: str,
    action: Literal["confirm", "correct", "resolve_discrepancy"],
    updates: dict | None = None,
    reason: str = "",
) -> dict:
    """Auditable confirmation/correction of stored clinical facts; never deletes history silently."""
    now = _now()
    updates = updates or {}
    before: dict = {}
    after: dict = {}

    if entity_type == "symptom":
        row = await db.get(ClinicalSymptom, entity_id)
        if row is None or row.user_id != cu.id:
            raise KeyError("Symptom not found")
        before = {
            "status": row.status, "severity": row.severity, "duration_text": row.duration_text,
            "frequency": row.frequency, "verification_status": row.verification_status,
        }
        if action == "confirm":
            row.verification_status = "clinician_confirmed"
        elif action == "resolve_discrepancy":
            row.verification_status = "clinician_confirmed"
            # Mark discrepancy memory items inactive
            mem_rows = list(await db.scalars(select(PatientMemoryItem).where(
                PatientMemoryItem.user_id == cu.id, PatientMemoryItem.fact_type == "discrepancy", PatientMemoryItem.active.is_(True)
            )))
            for m in mem_rows:
                c = _dec_json(cu, m.content_enc, {})
                if c.get("symptom_id") == entity_id:
                    m.active = False
        if "status" in updates and updates["status"] in ("active", "improving", "worsening", "resolved", "unknown"):
            row.status = updates["status"]
            if row.status == "resolved":
                row.resolved_at = now
        if "severity" in updates and updates["severity"] in ("mild", "moderate", "severe", "unknown", "not_assessed"):
            row.severity = updates["severity"]
        if "duration_text" in updates:
            row.duration_text = str(updates["duration_text"])[:80] if updates["duration_text"] else None
        if "frequency" in updates:
            row.frequency = str(updates["frequency"])[:80] if updates["frequency"] else None
        if "functional_impact" in updates:
            row.functional_impact_enc = cu.cipher.enc(str(updates["functional_impact"])[:200]) if updates["functional_impact"] else None
        if "verification_status" in updates and updates["verification_status"] in ("patient_reported", "clinician_reported", "clinician_confirmed", "unverified"):
            row.verification_status = updates["verification_status"]
        after = {
            "status": row.status, "severity": row.severity, "duration_text": row.duration_text,
            "frequency": row.frequency, "verification_status": row.verification_status,
        }

    elif entity_type == "medication":
        row = await db.get(Medication, entity_id)
        if row is None or row.user_id != cu.id:
            raise KeyError("Medication not found")
        before = {"name": cu.cipher.dec(row.name_enc), "dose": row.dose, "status": row.status, "verification_status": row.verification_status}
        if action == "confirm":
            row.verification_status = "clinician_confirmed"
        if "name" in updates and updates["name"]:
            row.name_enc = cu.cipher.enc(str(updates["name"])[:200])
        if "dose" in updates:
            row.dose = str(updates["dose"])[:40] if updates["dose"] else None
        if "status" in updates and updates["status"] in ("active", "stopped", "completed"):
            row.status = updates["status"]
        if "verification_status" in updates:
            row.verification_status = updates["verification_status"]
        after = {"name": cu.cipher.dec(row.name_enc), "dose": row.dose, "status": row.status, "verification_status": row.verification_status}

    elif entity_type == "condition":
        row = await db.get(Condition, entity_id)
        if row is None or row.user_id != cu.id:
            raise KeyError("Condition not found")
        before = {"display": cu.cipher.dec(row.display_enc), "status": row.status, "verification_status": row.verification_status}
        if action == "confirm":
            row.verification_status = "clinician_confirmed"
        if "status" in updates and updates["status"] in ("active", "resolved", "suspected"):
            row.status = updates["status"]
        if "display" in updates and updates["display"]:
            row.display_enc = cu.cipher.enc(str(updates["display"])[:160])
        after = {"display": cu.cipher.dec(row.display_enc), "status": row.status, "verification_status": row.verification_status}

    elif entity_type == "allergy":
        row = await db.get(Allergy, entity_id)
        if row is None or row.user_id != cu.id:
            raise KeyError("Allergy not found")
        before = {"substance": cu.cipher.dec(row.substance_enc), "verification_status": row.verification_status}
        if action == "confirm":
            row.verification_status = "clinician_confirmed"
        if "substance" in updates and updates["substance"]:
            row.substance_enc = cu.cipher.enc(str(updates["substance"])[:120])
        if "severity" in updates:
            row.severity = updates["severity"]
        after = {"substance": cu.cipher.dec(row.substance_enc), "verification_status": row.verification_status}

    elif entity_type == "medical_history":
        row = await db.get(MedicalHistoryEntry, entity_id)
        if row is None or row.user_id != cu.id:
            raise KeyError("History entry not found")
        before = {"description": cu.cipher.dec(row.description_enc), "status": row.status, "verification_status": row.verification_status}
        if action == "confirm":
            row.verification_status = "clinician_confirmed"
        if "description" in updates and updates["description"]:
            row.description_enc = cu.cipher.enc(str(updates["description"])[:300])
        if "status" in updates:
            row.status = updates["status"]
        after = {"description": cu.cipher.dec(row.description_enc), "status": row.status, "verification_status": row.verification_status}

    db.add(AuditLog(
        actor=cu.account_id,
        action=f"clinical_{action}",
        resource=entity_type,
        resource_id=entity_id,
        detail={"reason": reason[:200], "before": before, "after": after},
    ))
    await emit_event(
        db,
        cu,
        encounter_id=None,
        event_type="RECORD_CORRECTED",
        entity_type=entity_type,
        entity_id=entity_id,
        source="user_correction",
        verification_status=after.get("verification_status", "patient_reported"),
        payload={"action": action, "reason": reason[:200], "before": before, "after": after},
        occurred_at=now,
    )
    await db.commit()
    return {"entity_type": entity_type, "entity_id": entity_id, "action": action, "before": before, "after": after}
