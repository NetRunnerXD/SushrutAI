"""Atomic clinical facts with transcript span highlighting, strict provenance,
authorization guards on clinician-confirmed records, and conflict-safe offline sync.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clinical.schema import ClinicalExtractionPayload
from app.models import AuditEvent, ClinicalFact
from app.security.auth import CurrentUser

log = logging.getLogger("sushrut.clinical.facts")

VERIFICATION_LABELS = frozenset({
    "PATIENT_REPORTED",
    "CLINICIAN_CONFIRMED",
    "MEASURED",
    "IMPORTED_FROM_RECORD",
    "AI_INFERRED",
})

# Map legacy lowercase statuses cleanly to canonical Part 6.2 tags
_CANON_VER = {
    "clinician_confirmed": "CLINICIAN_CONFIRMED",
    "confirmed": "CLINICIAN_CONFIRMED",
    "clinician_reported": "CLINICIAN_CONFIRMED",
    "measured": "MEASURED",
    "device_measurement": "MEASURED",
    "imported": "IMPORTED_FROM_RECORD",
    "ai_hypothesis": "AI_INFERRED",
    "ai_inferred": "AI_INFERRED",
    "patient_reported": "PATIENT_REPORTED",
    "unverified": "PATIENT_REPORTED",
}


def canonical_verification(raw: str | None) -> str:
    if not raw:
        return "PATIENT_REPORTED"
    if raw in VERIFICATION_LABELS:
        return raw
    return _CANON_VER.get(raw.lower(), "PATIENT_REPORTED")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def find_transcript_span(transcript_text: str, quote: str) -> tuple[int | None, int | None]:
    """Locate exact [start, end] character offsets of `quote` inside `transcript_text` for UI highlighting."""
    t = transcript_text or ""
    q = (quote or "").strip()
    if not t or not q:
        return None, None
    idx = t.find(q)
    if idx >= 0:
        return idx, idx + len(q)
    low_t = t.lower()
    low_q = q.lower()
    idx = low_t.find(low_q)
    if idx >= 0:
        return idx, idx + len(q)
    # Try longest distinguishing phrase (>=2 words) from quote
    words = re.findall(r"[\w\u0980-\u09FF\u0900-\u097F']{2,}", q)
    for n in range(min(5, len(words)), 1, -1):
        sub = " ".join(words[:n]).lower()
        idx = low_t.find(sub)
        if idx >= 0:
            end_tok = words[-1].lower()
            end_idx = low_t.find(end_tok, idx)
            if end_idx >= idx:
                return idx, min(len(t), end_idx + len(end_tok))
            return idx, idx + len(sub)
    return None, None


async def record_clinical_facts(
    db: AsyncSession,
    cu: CurrentUser,
    *,
    encounter_id: str,
    conversation_id: str | None,
    turn_id: str | None,
    transcript_id: str | None,
    transcript_text: str,
    payload: ClinicalExtractionPayload,
    discrepancy_notes: list[str] | None = None,
) -> list[ClinicalFact]:
    """Emit atomic patient-level vs encounter-level ClinicalFact records with span highlights."""
    now = _now()
    created: list[ClinicalFact] = []

    async def _add_fact(
        *,
        scope: str,
        category: str,
        fact_key: str,
        value: dict,
        quote: str,
        verification_status: str,
        source_type: str,
        confidence: float = 0.85,
        temporal_info: dict | None = None,
        uncertainty_note: str | None = None,
        is_contradiction: bool = False,
    ) -> ClinicalFact:
        s0, s1 = find_transcript_span(transcript_text, quote)
        fact = ClinicalFact(
            user_id=cu.id,
            encounter_id=encounter_id if scope == "encounter" else None,
            conversation_id=conversation_id,
            source_turn_id=turn_id,
            transcript_id=transcript_id,
            scope=scope,
            category=category,
            fact_key=fact_key[:120],
            value_enc=cu.cipher.enc(json.dumps(value, ensure_ascii=False, default=str)),
            original_quote_enc=cu.cipher.enc(quote[:500]) if quote else None,
            span_start=s0,
            span_end=s1,
            speaker="patient",
            temporal_info=temporal_info,
            verification_status=canonical_verification(verification_status),
            source_type=source_type[:32],
            confidence=confidence,
            uncertainty_note=uncertainty_note[:200] if uncertainty_note else None,
            is_contradiction=is_contradiction,
            active=True,
            correction_history=[],
            observed_at=now,
        )
        db.add(fact)
        await db.flush()
        created.append(fact)
        return fact

    for sx in payload.symptoms:
        await _add_fact(
            scope="encounter",
            category="symptom",
            fact_key=f"symptom:{sx.name}",
            value={
                "name": sx.name,
                "status": sx.status,
                "progression": sx.progression,
                "severity": sx.severity,
                "duration": sx.duration,
                "frequency": sx.frequency,
                "triggers": sx.triggers,
                "relieving_factors": sx.relieving_factors,
                "functional_impact": sx.functional_impact,
            },
            quote=sx.original_statement,
            verification_status=sx.verification_status,
            source_type=sx.source,
            confidence=sx.confidence,
            temporal_info=sx.temporal.model_dump() if sx.temporal else None,
            uncertainty_note="Duration/onset uncertain" if sx.temporal and sx.temporal.is_temporal_uncertain else None,
        )
        if sx.functional_impact:
            await _add_fact(
                scope="encounter",
                category="functional",
                fact_key=f"functional:{sx.name}",
                value={"impact": sx.functional_impact, "symptom": sx.name},
                quote=sx.original_statement,
                verification_status=sx.verification_status,
                source_type=sx.source,
                confidence=sx.confidence,
            )

    for neg in payload.negated_symptoms:
        await _add_fact(
            scope="encounter",
            category="symptom",
            fact_key=f"negated:{neg.name}",
            value={"name": neg.name, "present": False},
            quote=neg.original_statement,
            verification_status="PATIENT_REPORTED",
            source_type=neg.source,
            confidence=neg.confidence,
        )

    for md in payload.medications:
        await _add_fact(
            scope="patient",
            category="medication",
            fact_key=f"med:{(md.normalized_medication_name or md.medication_name_reported).lower()}",
            value={
                "reported": md.medication_name_reported,
                "normalized": md.normalized_medication_name,
                "dosage": md.dosage,
                "unidentified": md.unidentified,
                "prescribed_or_self": md.prescribed_or_self,
            },
            quote=md.original_statement,
            verification_status=md.verification_status,
            source_type=md.source,
            confidence=md.confidence,
            uncertainty_note="Unidentified medicine — needs strip photo or pharmacist check" if md.unidentified else None,
        )

    for al in payload.allergies:
        await _add_fact(
            scope="patient",
            category="allergy",
            fact_key=f"allergy:{al.substance.lower()}",
            value={"substance": al.substance, "severity": al.severity},
            quote=al.original_statement,
            verification_status=al.verification_status,
            source_type=al.source,
            confidence=0.9,
        )

    for mh in payload.medical_history:
        await _add_fact(
            scope="patient",
            category="history",
            fact_key=f"{mh.category}:{(mh.normalized_name or mh.description).lower()}",
            value={"category": mh.category, "description": mh.description, "status": mh.status},
            quote=mh.original_statement,
            verification_status=mh.verification_status,
            source_type=mh.source,
            confidence=0.88,
        )

    for vm in payload.vital_measurements:
        await _add_fact(
            scope="encounter",
            category="vital",
            fact_key=f"vital:{vm.vital_type}",
            value={"type": vm.vital_type, "value": vm.value, "unit": vm.unit, "is_estimate": vm.is_estimate},
            quote=vm.original_statement,
            verification_status="PATIENT_REPORTED" if vm.is_estimate else "MEASURED",
            source_type=vm.source,
            confidence=0.65 if vm.is_estimate else 0.96,
            uncertainty_note="Patient estimate (not measured with device)" if vm.is_estimate else None,
        )

    for note in discrepancy_notes or []:
        await _add_fact(
            scope="encounter",
            category="contradiction",
            fact_key="contradiction:timeline_or_status",
            value={"note": note},
            quote=transcript_text[:280],
            verification_status="PATIENT_REPORTED",
            source_type="system_check",
            confidence=0.9,
            is_contradiction=True,
        )

    await db.flush()
    return created


async def correct_clinical_fact(
    db: AsyncSession,
    cu: CurrentUser,
    *,
    fact_id: str,
    new_value: dict,
    reason: str = "",
    actor_role: str = "patient",
    client_updated_at: datetime | None = None,
) -> dict:
    """Non-destructive clinical fact correction with authorization check and stale-offline sync guard."""
    row = await db.get(ClinicalFact, fact_id)
    if row is None or row.user_id != cu.id:
        raise KeyError("Clinical fact not found")

    if row.verification_status == "CLINICIAN_CONFIRMED" and actor_role != "clinician":
        raise PermissionError("Unauthorized: only an authorized clinician may modify clinician-confirmed records.")

    now = _now()
    # Offline sync conflict handling: reject stale client overwrites against newer server facts
    if client_updated_at is not None:
        naive_client = client_updated_at.replace(tzinfo=None)
        if naive_client < row.observed_at:
            raise ValueError(
                f"Sync conflict: server fact observed at {row.observed_at.isoformat()} is newer than offline edit ({naive_client.isoformat()})."
            )

    old_val = json.loads(cu.cipher.dec(row.value_enc) or "{}")
    history = list(row.correction_history or [])
    history.append({
        "at": now.isoformat(),
        "actor_role": actor_role,
        "previous_value": old_val,
        "reason": reason[:200],
    })

    # Non-destructive versioning: create new active ClinicalFact & point old row.superseded_by -> new_id
    new_row = ClinicalFact(
        user_id=cu.id,
        encounter_id=row.encounter_id,
        conversation_id=row.conversation_id,
        source_turn_id=row.source_turn_id,
        transcript_id=row.transcript_id,
        scope=row.scope,
        category=row.category,
        fact_key=row.fact_key,
        value_enc=cu.cipher.enc(json.dumps(new_value, ensure_ascii=False, default=str)),
        original_quote_enc=row.original_quote_enc,
        span_start=row.span_start,
        span_end=row.span_end,
        speaker=row.speaker,
        temporal_info=row.temporal_info,
        verification_status="CLINICIAN_CONFIRMED" if actor_role == "clinician" else row.verification_status,
        source_type="user_correction",
        confidence=1.0,
        uncertainty_note=None,
        is_contradiction=False,
        active=True,
        correction_history=history,
        observed_at=now,
    )
    db.add(new_row)
    await db.flush()
    row.active = False
    row.superseded_by = new_row.id

    db.add(AuditEvent(
        user_id=cu.id,
        actor_id=cu.account_id,
        actor_role=actor_role,
        action="CLINICAL_FACT_CORRECTED",
        entity_type="clinical_fact",
        entity_id=new_row.id,
        detail_enc=cu.cipher.enc(json.dumps({
            "superseded_id": row.id,
            "previous": old_val,
            "updated": new_value,
            "reason": reason[:200],
        }, ensure_ascii=False, default=str)),
        occurred_at=now,
    ))
    await db.commit()
    return {
        "previous_fact_id": row.id,
        "fact_id": new_row.id,
        "previous_value": old_val,
        "new_value": new_value,
        "verification_status": new_row.verification_status,
        "correction_history": history,
    }
