"""Personal Health Digital Twin (Longitudinal Patient & Family-Member Twin).

Aggregates an isolated, encrypted longitudinal health profile for a specific
patient or family member, strictly distinguishing:
  - Current/active state vs Historical/resolved data
  - Vital trajectories with timestamps, units, sources, and trend deltas
  - Symptom timelines, medications, lab reports, allergies, and consultations
  - FHIR R4-compatible Bundle export (Patient, Condition, MedicationStatement,
    Observation with LOINC, AllergyIntolerance)
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import scope_to
from app.models import (
    Allergy,
    ClinicalSymptom,
    Condition,
    Encounter,
    LabResult,
    Medication,
    PatientProfile,
    Vital,
)
from app.security.auth import CurrentUser


async def build_digital_twin(
    db: AsyncSession,
    cu: CurrentUser,
    *,
    member_name: str | None = None,
    relation: str = "self",
) -> dict[str, Any]:
    """Build a complete longitudinal Personal Health Digital Twin for `cu.id`."""
    await scope_to(db, cu.id, cu.account_id)

    prof = await db.get(PatientProfile, cu.id)
    age: int | None = None
    dob_iso: str | None = None
    if prof and prof.dob_enc:
        try:
            dob_iso = cu.cipher.dec(prof.dob_enc)
            age = int((datetime.now(timezone.utc).date() - date.fromisoformat(dob_iso)).days // 365.25)
        except ValueError:
            age = None

    # 1. Vitals: distinguish latest (current) vs historical readings
    vital_rows = list(await db.scalars(
        select(Vital).where(Vital.user_id == cu.id).order_by(Vital.measured_at.desc())
    ))
    vital_trajectories: dict[str, list[dict[str, Any]]] = {}
    current_vitals: dict[str, dict[str, Any]] = {}
    historical_vitals: dict[str, list[dict[str, Any]]] = {}

    for v in vital_rows:
        entry = {
            "id": v.id,
            "type": v.type,
            "value": v.value,
            "unit": v.unit,
            "measured_at": v.measured_at.isoformat() + "Z" if v.measured_at else None,
            "source": v.source,
            "is_estimate": bool(v.is_estimate),
        }
        vital_trajectories.setdefault(v.type, []).append(entry)

    for vtype, entries in vital_trajectories.items():
        current_vitals[vtype] = {**entries[0], "temporal_status": "current"}
        if len(entries) > 1:
            historical_vitals[vtype] = [{**e, "temporal_status": "historical"} for e in entries[1:]]

    # 2. Conditions: active vs historical/resolved
    cond_rows = list(await db.scalars(select(Condition).where(Condition.user_id == cu.id)))
    active_conditions: list[dict[str, Any]] = []
    historical_conditions: list[dict[str, Any]] = []
    for c in cond_rows:
        item = {
            "id": c.id,
            "name": cu.cipher.dec(c.display_enc),
            "status": c.status,
            "icd10": c.icd10,
            "snomed_code": c.snomed_code,
            "verification_status": c.verification_status,
        }
        if c.status == "active":
            active_conditions.append(item)
        else:
            historical_conditions.append(item)

    # 3. Symptom Timeline: active vs resolved/historical
    sx_rows = list(await db.scalars(
        select(ClinicalSymptom).where(ClinicalSymptom.user_id == cu.id).order_by(ClinicalSymptom.last_reported_at.desc())
    ))
    current_symptoms: list[dict[str, Any]] = []
    historical_symptoms: list[dict[str, Any]] = []
    for sx in sx_rows:
        s_item = {
            "id": sx.id,
            "name": sx.name,
            "status": sx.status,
            "severity": sx.severity,
            "duration_text": sx.duration_text,
            "first_reported_at": sx.first_reported_at.isoformat() + "Z" if sx.first_reported_at else None,
            "last_reported_at": sx.last_reported_at.isoformat() + "Z" if sx.last_reported_at else None,
            "resolved_at": sx.resolved_at.isoformat() + "Z" if sx.resolved_at else None,
        }
        if sx.status in ("resolved", "historical") or sx.resolved_at is not None:
            historical_symptoms.append(s_item)
        else:
            current_symptoms.append(s_item)

    # 4. Medications: active vs stopped/historical
    med_rows = list(await db.scalars(select(Medication).where(Medication.user_id == cu.id)))
    active_meds: list[dict[str, Any]] = []
    historical_meds: list[dict[str, Any]] = []
    for m in med_rows:
        m_item = {
            "id": m.id,
            "name": cu.cipher.dec(m.name_enc),
            "dose": m.dose,
            "status": m.status,
            "start_date": m.start_date.isoformat() if m.start_date else None,
            "end_date": m.end_date.isoformat() if m.end_date else None,
        }
        if m.status == "active":
            active_meds.append(m_item)
        else:
            historical_meds.append(m_item)

    # 5. Allergies
    allergy_rows = list(await db.scalars(select(Allergy).where(Allergy.user_id == cu.id)))
    allergies = [
        {
            "id": a.id,
            "substance": cu.cipher.dec(a.substance_enc),
            "type": a.type,
            "severity": a.severity,
        }
        for a in allergy_rows
    ]

    # 6. Lab Reports
    lab_rows = list(await db.scalars(
        select(LabResult).where(LabResult.user_id == cu.id).order_by(LabResult.observed_at.desc())
    ))
    lab_reports = [
        {
            "id": lr.id,
            "test_name": lr.test_name,
            "loinc_code": lr.loinc_code,
            "value": lr.value if lr.value is not None else lr.value_text,
            "unit": lr.unit,
            "flag": lr.flag,
            "observed_at": lr.observed_at.isoformat() + "Z" if lr.observed_at else None,
        }
        for lr in lab_rows[:40]
    ]

    # 7. Consultation History
    enc_rows = list(await db.scalars(
        select(Encounter).where(Encounter.user_id == cu.id).order_by(Encounter.started_at.desc())
    ))
    consultations = [
        {
            "id": e.id,
            "status": e.status,
            "channel": e.channel,
            "language": e.language,
            "urgency": e.urgency,
            "specialist": e.specialist,
            "started_at": e.started_at.isoformat() + "Z" if e.started_at else None,
        }
        for e in enc_rows[:25]
    ]

    # 8. FHIR R4-compatible Bundle
    fhir_entries: list[dict[str, Any]] = [
        {
            "resource": {
                "resourceType": "Patient",
                "id": cu.id,
                **({"name": [{"text": member_name}]} if member_name else {}),
                **({"gender": prof.sex} if prof and prof.sex else {}),
                **({"birthDate": dob_iso} if dob_iso else {}),
            }
        }
    ]
    for c in active_conditions + historical_conditions:
        fhir_entries.append({
            "resource": {
                "resourceType": "Condition",
                "id": c["id"],
                "clinicalStatus": {"coding": [{"code": "active" if c["status"] == "active" else "resolved"}]},
                "code": {"text": c["name"]},
                "subject": {"reference": f"Patient/{cu.id}"},
            }
        })
    for m in active_meds + historical_meds:
        fhir_entries.append({
            "resource": {
                "resourceType": "MedicationStatement",
                "id": m["id"],
                "status": "active" if m["status"] == "active" else "stopped",
                "medicationCodeableConcept": {"text": m["name"]},
                "subject": {"reference": f"Patient/{cu.id}"},
            }
        })
    for a in allergies:
        fhir_entries.append({
            "resource": {
                "resourceType": "AllergyIntolerance",
                "id": a["id"],
                "code": {"text": a["substance"]},
                "patient": {"reference": f"Patient/{cu.id}"},
            }
        })
    for lr in lab_reports[:20]:
        fhir_entries.append({
            "resource": {
                "resourceType": "Observation",
                "id": lr["id"],
                "status": "final",
                "code": {
                    "text": lr["test_name"],
                    **({"coding": [{"system": "http://loinc.org", "code": lr["loinc_code"]}]} if lr["loinc_code"] else {}),
                },
                "subject": {"reference": f"Patient/{cu.id}"},
                **({"effectiveDateTime": lr["observed_at"]} if lr["observed_at"] else {}),
            }
        })

    return {
        "patient_id": cu.id,
        "member_name": member_name or "Self",
        "relation": relation,
        "demographics": {
            "age": age,
            "sex": prof.sex if prof else None,
            "blood_group": prof.blood_group if prof else None,
            "height_cm": prof.height_cm if prof else None,
        },
        "current_state": {
            "vitals": current_vitals,
            "symptoms": current_symptoms,
            "conditions": active_conditions,
            "medications": active_meds,
        },
        "historical_state": {
            "vitals": historical_vitals,
            "symptoms": historical_symptoms,
            "conditions": historical_conditions,
            "medications": historical_meds,
        },
        "allergies": allergies,
        "lab_reports": lab_reports,
        "consultations": consultations,
        "fhir_r4_bundle": {
            "resourceType": "Bundle",
            "type": "collection",
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "entry": fhir_entries,
        },
    }
