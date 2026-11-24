from __future__ import annotations

import json
import shutil
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clinical.bmi import bmi, bmi_category
from app.config import get_settings
from app.db import get_db, scope_to
from app.models import (
    Allergy, AuditLog, CareRecord, ClinicalEvent, ClinicalRecommendation, ClinicalSymptom, Condition, Consent,
    ConversationTurn, DataRequest, Document, DoseLog, Encounter, Episode, FollowUp, FollowUpQuestion, HealthFact,
    HouseholdMember, Job, LabResult, MedicalHistoryEntry, Medication, Message, PatientMemoryItem, PatientProfile,
    Pregnancy, Prescription, SafetyAlert, SymptomObservation, TriageSession, User, UserSettings, VisitQuestion, Vital,
)
from app.models import _now as utcnow
from app.security.auth import CurrentUser, current_user
from app.langs import LangCode

router = APIRouter(prefix="/me", tags=["profile"])

BloodGroup = Literal["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]
VITAL_UNITS = {"bp_sys": "mmHg", "bp_dia": "mmHg", "hr": "bpm", "spo2": "%", "temp": "°C", "rr": "/min",
               "glucose_f": "mg/dL", "glucose_pp": "mg/dL", "glucose_r": "mg/dL", "weight": "kg", "waist": "cm"}
VITAL_RANGES = {"bp_sys": (50, 260), "bp_dia": (30, 160), "hr": (25, 250), "spo2": (50, 100), "temp": (30, 44),
                "rr": (4, 70), "glucose_f": (20, 700), "glucose_pp": (20, 900), "glucose_r": (20, 900),
                "weight": (0.5, 350), "waist": (20, 250)}


class ProfileIn(BaseModel):
    dob: date | None = None
    sex: Literal["male", "female", "intersex"] | None = None
    gender: str | None = Field(None, max_length=40)
    blood_group: BloodGroup | None = None
    height_cm: float | None = Field(None, ge=30, le=250)
    state_code: str | None = Field(None, max_length=4)
    district: str | None = Field(None, max_length=64)
    diet: Literal["veg", "non_veg", "eggetarian", "vegan", "jain"] | None = None
    smoking_status: Literal["never", "former", "current"] | None = None
    pack_years: float | None = Field(None, ge=0, le=200)
    tobacco_chewing: bool | None = None
    alcohol_units_wk: float | None = Field(None, ge=0, le=300)
    activity_level: Literal["sedentary", "light", "moderate", "active"] | None = None
    pregnancy_status: Literal["not_pregnant", "pregnant", "possibly_pregnant", "na"] | None = None
    lactating: bool | None = None
    surgeries: list[str] | None = None
    family_history: list[str] | None = None
    emergency_contact: str | None = Field(None, max_length=80)
    occupation: str | None = Field(None, max_length=80)
    sleep: str | None = Field(None, max_length=80)
    hydration: str | None = Field(None, max_length=80)
    healthcare_access: str | None = Field(None, max_length=40)
    literacy_level: Literal["fluent", "limited", "non_reader", "not_assessed"] | None = None
    preferred_lang: LangCode | None = None


_ENC_FIELDS = {
    "dob": "dob_enc",
    "gender": "gender_enc",
    "emergency_contact": "emergency_contact_enc",
    "occupation": "occupation_enc",
    "sleep": "sleep_enc",
    "hydration": "hydration_enc",
}
_ENC_JSON = {"surgeries": "surgeries_enc", "family_history": "family_history_enc"}


async def _profile_out(db: AsyncSession, cu: CurrentUser) -> dict:
    p = await db.get(PatientProfile, cu.id)
    out: dict = {"user_id": cu.id, "is_guest": cu.user.is_guest, "preferred_lang": cu.user.preferred_lang}
    if p:
        for f in ("sex", "blood_group", "height_cm", "state_code", "district", "diet", "smoking_status",
                  "pack_years", "tobacco_chewing", "alcohol_units_wk", "activity_level", "pregnancy_status",
                  "lactating", "healthcare_access", "literacy_level"):
            out[f] = getattr(p, f)
        for f, col in _ENC_FIELDS.items():
            out[f] = cu.cipher.dec(getattr(p, col))
        for f, col in _ENC_JSON.items():
            v = cu.cipher.dec(getattr(p, col))
            out[f] = json.loads(v) if v else []
    w = (await db.scalars(select(Vital).where(Vital.user_id == cu.id, Vital.type == "weight")
                          .order_by(Vital.measured_at.desc()))).first()
    if w and p and p.height_cm:
        b = bmi(w.value, p.height_cm)
        out["bmi"] = {"value": b, "category": bmi_category(b), "cutoffs": "Asian-Indian (23 / 25)"}
    return out


@router.get("/profile")
async def get_profile(cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    return await _profile_out(db, cu)


@router.put("/profile")
async def put_profile(body: ProfileIn, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    p = await db.get(PatientProfile, cu.id) or PatientProfile(user_id=cu.id)
    data = body.model_dump(exclude_unset=True)
    if "preferred_lang" in data:
        cu.user.preferred_lang = data.pop("preferred_lang")
    for k, v in data.items():
        if k in _ENC_FIELDS:
            setattr(p, _ENC_FIELDS[k], cu.cipher.enc(v.isoformat() if isinstance(v, date) else v))
        elif k in _ENC_JSON:
            setattr(p, _ENC_JSON[k], cu.cipher.enc(json.dumps(v or [], ensure_ascii=False)))
        else:
            setattr(p, k, v)
    db.add(p)
    await db.commit()
    return await _profile_out(db, cu)


# ---------------------------------------------------------------- allergies / conditions / meds
class AllergyIn(BaseModel):
    substance: str = Field(min_length=1, max_length=120)
    type: Literal["drug", "food", "other"] = "drug"
    reaction: str | None = Field(None, max_length=200)
    severity: Literal["mild", "moderate", "severe", "anaphylaxis"] | None = None


class ConditionIn(BaseModel):
    display: str = Field(min_length=1, max_length=160)
    snomed_code: str | None = None
    icd10: str | None = None
    status: Literal["active", "resolved", "suspected"] = "active"
    onset_date: date | None = None


class MedicationIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    product_id: str | None = None
    generic_id: str | None = None
    dose: str | None = None
    frequency: dict | None = None
    timing: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    status: Literal["active", "stopped", "completed"] = "active"


@router.get("/allergies")
async def list_allergies(cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    return [{"id": a.id, "substance": cu.cipher.dec(a.substance_enc), "type": a.type,
             "reaction": cu.cipher.dec(a.reaction_enc), "severity": a.severity}
            for a in await db.scalars(select(Allergy).where(Allergy.user_id == cu.id))]


@router.post("/allergies", status_code=201)
async def add_allergy(body: AllergyIn, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    a = Allergy(user_id=cu.id, substance_enc=cu.cipher.enc(body.substance), type=body.type,
                reaction_enc=cu.cipher.enc(body.reaction), severity=body.severity)
    db.add(a)
    await db.commit()
    return {"id": a.id}


@router.delete("/allergies/{aid}", status_code=204)
async def del_allergy(aid: str, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await db.execute(delete(Allergy).where(Allergy.id == aid, Allergy.user_id == cu.id))
    await db.commit()


@router.get("/conditions")
async def list_conditions(cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    return [{"id": c.id, "display": cu.cipher.dec(c.display_enc), "snomed_code": c.snomed_code, "icd10": c.icd10,
             "status": c.status, "onset_date": c.onset_date, "source": c.source}
            for c in await db.scalars(select(Condition).where(Condition.user_id == cu.id))]


@router.post("/conditions", status_code=201)
async def add_condition(body: ConditionIn, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    c = Condition(user_id=cu.id, display_enc=cu.cipher.enc(body.display), snomed_code=body.snomed_code,
                  icd10=body.icd10, status=body.status, onset_date=body.onset_date)
    db.add(c)
    await db.commit()
    return {"id": c.id}


@router.delete("/conditions/{cid}", status_code=204)
async def del_condition(cid: str, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await db.execute(delete(Condition).where(Condition.id == cid, Condition.user_id == cu.id))
    await db.commit()


def _med_out(m: Medication, cu: CurrentUser) -> dict:
    return {"id": m.id, "name": cu.cipher.dec(m.name_enc), "product_id": m.product_id, "generic_id": m.generic_id,
            "dose": m.dose, "frequency": m.frequency, "timing": m.timing, "start_date": m.start_date,
            "end_date": m.end_date, "status": m.status, "prescription_item_id": m.prescription_item_id}


@router.get("/medications")
async def list_meds(cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    return [_med_out(m, cu) for m in await db.scalars(select(Medication).where(Medication.user_id == cu.id))]


@router.post("/medications", status_code=201)
async def add_med(body: MedicationIn, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    d = body.model_dump()
    m = Medication(user_id=cu.id, name_enc=cu.cipher.enc(d.pop("name")), **d)
    db.add(m)
    from app.memory.trajectory import start_trajectory
    start_trajectory(db, cu, [body.name])  # "is it better since starting …?" on day 1 and 2
    await db.commit()
    from app.medicine.checks import check_new_medicine
    # allergy and interaction warnings for the medicine just added (shown at once; nothing is blocked)
    return {**_med_out(m, cu), "warnings": await check_new_medicine(db, cu, body.name, body.product_id, exclude_id=m.id)}


@router.patch("/medications/{mid}")
async def patch_med(mid: str, body: MedicationIn, cu: CurrentUser = Depends(current_user),
                    db: AsyncSession = Depends(get_db)):
    m = await db.get(Medication, mid)
    if m is None or m.user_id != cu.id:
        raise HTTPException(404, "Not found")
    d = body.model_dump(exclude_unset=True)
    if "name" in d:
        m.name_enc = cu.cipher.enc(d.pop("name"))
    for k, v in d.items():
        setattr(m, k, v)
    await db.commit()
    return _med_out(m, cu)


@router.delete("/medications/{mid}", status_code=204)
async def del_med(mid: str, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await db.execute(delete(Medication).where(Medication.id == mid, Medication.user_id == cu.id))
    await db.commit()


# ---------------------------------------------------------------- vitals
class VitalIn(BaseModel):
    type: Literal["bp_sys", "bp_dia", "hr", "spo2", "temp", "rr", "glucose_f", "glucose_pp", "glucose_r",
                  "weight", "waist"]
    value: float
    measured_at: datetime | None = None


@router.get("/vitals")
async def list_vitals(type: str | None = None, cu: CurrentUser = Depends(current_user),
                      db: AsyncSession = Depends(get_db)):
    q = select(Vital).where(Vital.user_id == cu.id)
    if type:
        q = q.where(Vital.type == type)
    return [{"id": v.id, "type": v.type, "value": v.value, "unit": v.unit, "measured_at": v.measured_at}
            for v in await db.scalars(q.order_by(Vital.measured_at))]


@router.post("/vitals", status_code=201)
async def add_vitals(body: list[VitalIn], cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    for v in body:
        lo, hi = VITAL_RANGES[v.type]
        if not lo <= v.value <= hi:
            raise HTTPException(422, f"{v.type}={v.value} is outside the plausible range {lo}–{hi} {VITAL_UNITS[v.type]}")
    for v in body:
        db.add(Vital(user_id=cu.id, type=v.type, value=v.value, unit=VITAL_UNITS[v.type],
                     measured_at=(v.measured_at.replace(tzinfo=None) if v.measured_at else utcnow())))
    await db.commit()
    return {"added": len(body)}


# ---------------------------------------------------------------- DPDP rights: export & erasure
@router.get("/export")
async def export(cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Right to access / portability: everything we hold about the user, decrypted."""
    from app.models import ChatSession, CheckIn

    meds = [_med_out(m, cu) for m in await db.scalars(select(Medication).where(Medication.user_id == cu.id))]
    rx = [{"id": r.id, "status": r.status, "created_at": r.created_at, "parsed": json.loads(cu.cipher.dec(r.parsed_enc))}
          for r in await db.scalars(select(Prescription).where(Prescription.user_id == cu.id))]
    chats = []
    for s in await db.scalars(select(ChatSession).where(ChatSession.user_id == cu.id)):
        msgs = await db.scalars(select(Message).where(Message.session_id == s.id).order_by(Message.created_at))
        chats.append({"id": s.id, "started_at": s.started_at,
                      "messages": [{"role": m.role, "content": cu.cipher.dec(m.content_enc), "at": m.created_at}
                                   for m in msgs]})
    encounters = [
        {
            "id": e.id, "status": e.status, "channel": e.channel, "language": e.language,
            "chief_complaint": cu.cipher.dec(e.chief_complaint_enc),
            "summary": cu.cipher.dec(e.summary_enc),
            "urgency": e.urgency, "specialist": e.specialist,
            "started_at": e.started_at, "ended_at": e.ended_at,
        }
        for e in await db.scalars(select(Encounter).where(Encounter.user_id == cu.id))
    ]
    symptoms = [
        {
            "id": sx.id, "name": sx.name, "status": sx.status, "progression": sx.progression,
            "onset_text": sx.onset_text, "duration_text": sx.duration_text, "frequency": sx.frequency,
            "severity": sx.severity, "verification_status": sx.verification_status,
            "original_statement": cu.cipher.dec(sx.original_statement_enc),
            "functional_impact": cu.cipher.dec(sx.functional_impact_enc),
            "first_reported_at": sx.first_reported_at, "last_reported_at": sx.last_reported_at,
        }
        for sx in await db.scalars(select(ClinicalSymptom).where(ClinicalSymptom.user_id == cu.id))
    ]
    events = [
        {
            "id": ev.id, "event_type": ev.event_type, "entity_type": ev.entity_type, "entity_id": ev.entity_id,
            "source": ev.source, "verification_status": ev.verification_status, "discrepancy_flag": ev.discrepancy_flag,
            "payload": json.loads(cu.cipher.dec(ev.payload_enc) or "{}"), "occurred_at": ev.occurred_at,
        }
        for ev in await db.scalars(select(ClinicalEvent).where(ClinicalEvent.user_id == cu.id))
    ]
    med_hist = [
        {
            "id": mh.id, "category": mh.category, "description": cu.cipher.dec(mh.description_enc),
            "status": mh.status, "verification_status": mh.verification_status, "recorded_at": mh.recorded_at,
        }
        for mh in await db.scalars(select(MedicalHistoryEntry).where(MedicalHistoryEntry.user_id == cu.id))
    ]
    from app.models import ClinicalAssessment, ClinicalFact, ClinicalInvestigation, Transcript
    transcripts = [
        {
            "id": tr.id, "encounter_id": tr.encounter_id, "speaker": tr.speaker,
            "raw_text": cu.cipher.dec(tr.raw_text_enc),
            "corrected_text": cu.cipher.dec(tr.corrected_text_enc),
            "primary_language": tr.primary_language, "created_at": tr.created_at,
        }
        for tr in await db.scalars(select(Transcript).where(Transcript.user_id == cu.id))
    ]
    facts = [
        {
            "id": f.id, "scope": f.scope, "category": f.category, "fact_key": f.fact_key,
            "value": json.loads(cu.cipher.dec(f.value_enc) or "{}"),
            "original_quote": cu.cipher.dec(f.original_quote_enc),
            "verification_status": f.verification_status, "observed_at": f.observed_at,
        }
        for f in await db.scalars(select(ClinicalFact).where(ClinicalFact.user_id == cu.id))
    ]
    assessments = [
        {
            "id": a.id, "encounter_id": a.encounter_id, "summary": cu.cipher.dec(a.summary_enc),
            "safety_status": a.safety_status, "interview_outcome": a.interview_outcome, "created_at": a.created_at,
        }
        for a in await db.scalars(select(ClinicalAssessment).where(ClinicalAssessment.user_id == cu.id))
    ]
    investigations = [
        {
            "id": iv.id, "name": iv.name, "purpose_category": iv.purpose_category, "urgency": iv.urgency,
            "rationale": cu.cipher.dec(iv.rationale_enc), "already_in_records": iv.already_in_records,
        }
        for iv in await db.scalars(select(ClinicalInvestigation).where(ClinicalInvestigation.user_id == cu.id))
    ]
    db.add(DataRequest(user_id=cu.id, type="access", status="closed", closed_at=utcnow()))
    await db.commit()
    return {
        "exported_at": utcnow().isoformat(),
        "profile": await _profile_out(db, cu),
        "allergies": await list_allergies(cu, db),
        "conditions": await list_conditions(cu, db),
        "medications": meds,
        "vitals": await list_vitals(None, cu, db),
        "prescriptions": rx,
        "chats": chats,
        "encounters": encounters,
        "symptoms": symptoms,
        "clinical_events": events,
        "medical_history": med_hist,
        "transcripts": transcripts,
        "clinical_facts": facts,
        "assessments": assessments,
        "investigations": investigations,
        "consents": [{"purpose": c.purpose, "granted_at": c.granted_at, "withdrawn_at": c.withdrawn_at}
                     for c in await db.scalars(select(Consent).where(Consent.user_id == cu.id))],
    }


async def erase_user(db: AsyncSession, uid: str, actor: str) -> None:
    """Delete every row of one user and their files; the DEK goes with the users row (crypto-shredding).
    The caller must have scoped the session to `uid` (scope_to) and commits."""
    from app.models import (
        AuditEvent,
        CalendarEvent,
        ChatSession,
        CheckIn,
        ClinicalAssessment,
        ClinicalFact,
        ClinicalInvestigation,
        DifferentialConsideration,
        Transcript,
        TranscriptVersion,
    )

    sessions = select(ChatSession.id).where(ChatSession.user_id == uid)
    await db.execute(delete(Message).where(Message.session_id.in_(sessions)))
    for model in (DifferentialConsideration, ClinicalAssessment, ClinicalInvestigation, ClinicalFact,
                  TranscriptVersion, Transcript, AuditEvent, SymptomObservation, ClinicalSymptom,
                  ConversationTurn, ClinicalRecommendation, FollowUpQuestion,
                  ClinicalEvent, SafetyAlert, MedicalHistoryEntry, PatientMemoryItem, Encounter, HealthFact, Episode,
                  CalendarEvent, ChatSession, TriageSession, Prescription, Job, Document, Vital, Medication, Condition,
                  Allergy, Consent, PatientProfile, LabResult, UserSettings, DoseLog, HouseholdMember, CheckIn,
                  CareRecord, Pregnancy, VisitQuestion, FollowUp):
        await db.execute(delete(model).where(model.user_id == uid))
    await db.execute(delete(User).where(User.id == uid))
    db.add(AuditLog(actor=actor, action="erasure", resource="user", resource_id=uid))
    db.add(DataRequest(user_id=uid, type="erasure", status="closed", closed_at=utcnow()))
    shutil.rmtree(get_settings().storage_dir / uid, ignore_errors=True)


@router.delete("", status_code=204)
async def erase(cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Right to erasure for the whole account: the account holder and every family profile they manage."""
    aid = cu.account_id
    members = list(await db.scalars(select(HouseholdMember.member_id).where(HouseholdMember.user_id == aid)))
    for mid in members:
        await scope_to(db, mid, aid)
        await erase_user(db, mid, aid)
    await scope_to(db, aid)
    await erase_user(db, aid, aid)
    await db.commit()
