"""Settings, medication reminders (iCalendar) and FHIR R4 export of the patient record."""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import local_today
from app.db import get_db
from app.models import (
    Allergy, Condition, DoseLog, LabResult, Medication, PatientProfile, Prescription, Vital,
)
from app.models import _now as utcnow
from app.llm.providers import model_options
from app.security.auth import CurrentUser, current_user
from app.security.settings import get_settings_row, server_offline

router = APIRouter(prefix="/me", tags=["records"])


# ---------------------------------------------------------------- settings
class SettingsIn(BaseModel):
    offline_mode: bool | None = None
    reminder_times: dict[str, str] | None = Field(None, description='{"morning":"08:00","afternoon":"14:00","night":"21:00"}')
    large_text: bool | None = None
    voice_autoplay: bool | None = None
    model_pref: Literal["auto", "balanced", "fast", "instant", "cloud", "gemini", "gemini_fast"] | None = None


def _settings_out(s) -> dict:
    return {"offline_mode": s.offline_mode or server_offline(), "offline_forced_by_server": server_offline(),
            "reminder_times": s.reminder_times, "large_text": s.large_text, "voice_autoplay": s.voice_autoplay,
            "model_pref": s.model_pref or "auto", "model_options": model_options()}


@router.get("/settings")
async def get_settings(cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    s = await get_settings_row(db, cu.account_id)
    await db.commit()
    return _settings_out(s)


@router.put("/settings")
async def put_settings(body: SettingsIn, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    s = await get_settings_row(db, cu.account_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        if k == "reminder_times" and v:
            for slot, t in v.items():
                datetime.strptime(t, "%H:%M")  # validates HH:MM
            v = {**s.reminder_times, **v}
        setattr(s, k, v)
    await db.commit()
    return _settings_out(s)


# ---------------------------------------------------------------- reminders
def _ics_escape(t: str) -> str:
    return t.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def reminder_slots(frequency: dict | None) -> list[str]:
    """Which daily slots a medicine needs, from the parsed frequency (1-0-1, BD, TDS, HS…)."""
    if not frequency or frequency.get("per_day") is None:
        return []  # SOS / weekly / unknown: no fixed daily reminder
    slots = [s for s in ("morning", "afternoon", "night") if (frequency.get(s) or 0) > 0]
    return slots or ["morning"]


def build_ics(meds: list[dict], times: dict[str, str], today: date) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//SushrutAI//Medication reminders//EN", "CALSCALE:GREGORIAN"]
    stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
    for m in meds:
        start = m.get("start_date") or today
        for slot in reminder_slots(m.get("frequency")):
            hh, mm = times.get(slot, "08:00").split(":")
            dt = datetime(start.year, start.month, start.day, int(hh), int(mm))
            rrule = "RRULE:FREQ=DAILY"
            if m.get("end_date"):
                rrule += f";UNTIL={m['end_date'].strftime('%Y%m%d')}T235959"
            timing = {"before_food": " — before food", "after_food": " — after food",
                      "empty_stomach": " — on an empty stomach", "bedtime": " — at bedtime"}.get(m.get("timing") or "", "")
            lines += ["BEGIN:VEVENT", f"UID:{uuid.uuid5(uuid.NAMESPACE_URL, m['id'] + slot)}@sushrutai",
                      f"DTSTAMP:{stamp}", f"DTSTART:{dt.strftime('%Y%m%dT%H%M%S')}", rrule,
                      f"SUMMARY:{_ics_escape('Take ' + m['name'] + timing)}",
                      f"DESCRIPTION:{_ics_escape('As prescribed by your doctor. Reminder from SushrutAI.')}",
                      "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_ics_escape('Take ' + m['name'])}",
                      "TRIGGER:PT0M", "END:VALARM", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


async def _active_meds(db: AsyncSession, cu: CurrentUser) -> list[dict]:
    today = local_today()
    out = []
    for m in await db.scalars(select(Medication).where(Medication.user_id == cu.id, Medication.status == "active")):
        if m.end_date and m.end_date < today:
            continue
        out.append({"id": m.id, "name": cu.cipher.dec(m.name_enc), "frequency": m.frequency, "timing": m.timing,
                    "start_date": m.start_date, "end_date": m.end_date})
    return out


@router.get("/reminders")
async def reminders(cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Today's schedule (the web app shows these and fires browser notifications)."""
    s = await get_settings_row(db, cu.account_id)
    await db.commit()
    sched = []
    for m in await _active_meds(db, cu):
        for slot in reminder_slots(m["frequency"]):
            sched.append({"medication_id": m["id"], "name": m["name"], "slot": slot,
                          "time": s.reminder_times.get(slot), "timing": m["timing"]})
        if not reminder_slots(m["frequency"]):
            sched.append({"medication_id": m["id"], "name": m["name"], "slot": "as_needed", "time": None,
                          "timing": m["timing"]})
    return sorted(sched, key=lambda x: x["time"] or "99")


@router.get("/reminders.ics")
async def reminders_ics(cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Import into Google Calendar / phone calendar — reminders keep working fully offline."""
    s = await get_settings_row(db, cu.account_id)
    await db.commit()
    ics = build_ics(await _active_meds(db, cu), s.reminder_times, local_today())
    from app.routers.care import care_ics_events, schedule_for  # vaccines & pregnancy check-ups
    care = care_ics_events(await schedule_for(db, cu), local_today())
    if care:
        ics = ics.replace("END:VCALENDAR", "\r\n".join(care) + "\r\nEND:VCALENDAR")
    return Response(ics, media_type="text/calendar",
                    headers={"Content-Disposition": 'attachment; filename="sushrutai-reminders.ics"'})


# ---------------------------------------------------------------- dose tracker
class DoseIn(BaseModel):
    medication_id: str
    slot: str = Field(pattern=r"^(morning|afternoon|night|as_needed)$")
    status: str = Field(pattern=r"^(taken|skipped|clear)$")
    day: date | None = None


@router.get("/doses")
async def doses(day: date | None = None, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """The day's scheduled doses with taken/skipped status, plus adherence for the last 7 days."""
    d = day or local_today()
    s = await get_settings_row(db, cu.account_id)
    await db.commit()
    logs = {(l.medication_id, l.slot): l.status for l in await db.scalars(
        select(DoseLog).where(DoseLog.user_id == cu.id, DoseLog.day == d))}
    items = []
    for m in await _active_meds(db, cu):
        if m["start_date"] and m["start_date"] > d:
            continue
        slots = reminder_slots(m["frequency"]) or ["as_needed"]
        for slot in slots:
            days_left = (m["end_date"] - d).days if m["end_date"] else None
            items.append({"medication_id": m["id"], "name": m["name"], "slot": slot, "time": s.reminder_times.get(slot),
                          "timing": m["timing"], "frequency": m["frequency"], "status": logs.get((m["id"], slot)),
                          "days_left": days_left})
    items.sort(key=lambda x: x["time"] or "99")
    week_start = d - timedelta(days=6)
    week = list(await db.scalars(select(DoseLog).where(DoseLog.user_id == cu.id, DoseLog.day >= week_start,
                                                        DoseLog.day <= d)))
    taken = sum(1 for l in week if l.status == "taken")
    scheduled = [i for i in items if i["slot"] != "as_needed"]
    return {"day": d, "items": items,
            "progress": {"done": sum(1 for i in scheduled if i["status"] == "taken"), "total": len(scheduled)},
            "week": {"taken": taken, "logged": len(week)}}


@router.post("/doses")
async def log_dose(body: DoseIn, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    d = body.day or local_today()
    row = (await db.scalars(select(DoseLog).where(DoseLog.user_id == cu.id, DoseLog.medication_id == body.medication_id,
                                                  DoseLog.slot == body.slot, DoseLog.day == d))).first()
    if body.status == "clear":
        if row:
            await db.delete(row)
    elif row:
        row.status, row.at = body.status, utcnow()
    else:
        db.add(DoseLog(user_id=cu.id, medication_id=body.medication_id, slot=body.slot, day=d, status=body.status))
    await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- emergency card
@router.get("/emergency-card")
async def emergency_card(cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """The few facts responders need — shown on the SOS sheet and cached for offline use."""
    p = await db.get(PatientProfile, cu.id)
    dob = cu.cipher.dec(p.dob_enc) if p else None
    age = None
    if dob:
        try:
            age = int((local_today() - date.fromisoformat(dob)).days // 365.25)
        except ValueError:
            age = None
    return {
        "age": age, "sex": p.sex if p else None, "blood_group": p.blood_group if p else None,
        "emergency_contact": cu.cipher.dec(p.emergency_contact_enc) if p else None,
        "pregnancy_status": p.pregnancy_status if p else None,
        "allergies": [cu.cipher.dec(a.substance_enc) for a in await db.scalars(select(Allergy).where(Allergy.user_id == cu.id))],
        "conditions": [cu.cipher.dec(c.display_enc) for c in await db.scalars(
            select(Condition).where(Condition.user_id == cu.id, Condition.status == "active"))],
        "medicines": [m["name"] for m in await _active_meds(db, cu)],
    }


# ---------------------------------------------------------------- timeline + FHIR
@router.get("/timeline")
async def timeline(cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    ev: list[dict] = []
    for r in await db.scalars(select(Prescription).where(Prescription.user_id == cu.id)):
        p = json.loads(cu.cipher.dec(r.parsed_enc))
        ev.append({"at": r.created_at, "type": "prescription", "id": r.id, "status": r.status,
                   "summary": ", ".join(m["name"] for m in p.get("medications", []))[:200]})
    for v in await db.scalars(select(Vital).where(Vital.user_id == cu.id)):
        ev.append({"at": v.measured_at, "type": "vital", "summary": f"{v.type} {v.value:g} {v.unit}"})
    for lr in await db.scalars(select(LabResult).where(LabResult.user_id == cu.id)):
        ev.append({"at": lr.observed_at, "type": "lab", "summary": f"{lr.test_name} {lr.value_text or lr.value} "
                                                                  f"{lr.unit or ''} {lr.flag or ''}".strip()})
    # the health memory: episodes ("fever, body ache") and what was said about them (readings are listed above)
    from app.memory.store import timeline_items
    for it in await timeline_items(db, cu):
        if it["type"] == "episode":
            ev.append({"at": it["at"], "type": "episode", "id": it["id"], "status": it["status"],
                       "summary": it["title"] + (f" — {', '.join(it['tags'])}" if it["tags"] else "")})
        elif it["kind"] not in ("vital", "preference", "feedback"):
            ev.append({"at": it["at"], "type": "health", "id": it["id"], "kind": it["kind"],
                       "summary": it["line"].split(": ", 1)[-1]})
    return sorted(ev, key=lambda e: e["at"], reverse=True)


VITAL_LOINC = {"bp_sys": ("8480-6", "Systolic blood pressure"), "bp_dia": ("8462-4", "Diastolic blood pressure"),
               "hr": ("8867-4", "Heart rate"), "spo2": ("59408-5", "Oxygen saturation by pulse oximetry"),
               "temp": ("8310-5", "Body temperature"), "rr": ("9279-1", "Respiratory rate"),
               "weight": ("29463-7", "Body weight"), "waist": ("8280-0", "Waist circumference"),
               "glucose_f": ("1558-6", "Fasting glucose"), "glucose_pp": ("1521-4", "Glucose 2 h post meal"),
               "glucose_r": ("2345-7", "Glucose")}
UCUM = {"mmHg": "mm[Hg]", "bpm": "/min", "%": "%", "°C": "Cel", "/min": "/min", "kg": "kg", "cm": "cm",
        "mg/dL": "mg/dL"}


def validate_fhir_bundle(bundle: dict) -> list[str]:
    """Validate structural conformance of an HL7 FHIR R4 collection Bundle."""
    errors: list[str] = []
    if bundle.get("resourceType") != "Bundle":
        errors.append("Root resourceType must be Bundle")
    if bundle.get("type") not in ("collection", "document", "transaction"):
        errors.append("Bundle.type must be valid FHIR bundle type")
    for idx, entry in enumerate(bundle.get("entry") or []):
        res = entry.get("resource") or {}
        rt = res.get("resourceType")
        if not rt:
            errors.append(f"entry[{idx}] missing resourceType")
            continue
        if rt == "Patient" and not res.get("id"):
            errors.append(f"entry[{idx}] Patient missing id")
        elif rt == "Condition" and (not res.get("code") or not res.get("subject")):
            errors.append(f"entry[{idx}] Condition missing code or subject")
        elif rt == "Observation" and (not res.get("code") or not res.get("status") or not res.get("subject")):
            errors.append(f"entry[{idx}] Observation missing code/status/subject")
        elif rt == "MedicationStatement" and (not res.get("medicationCodeableConcept") or not res.get("subject")):
            errors.append(f"entry[{idx}] MedicationStatement missing medication/subject")
        elif rt == "MedicationRequest" and (not res.get("medicationCodeableConcept") or not res.get("subject") or not res.get("intent")):
            errors.append(f"entry[{idx}] MedicationRequest missing fields")
        elif rt == "AllergyIntolerance" and (not res.get("code") or not res.get("patient")):
            errors.append(f"entry[{idx}] AllergyIntolerance missing code/patient")
        elif rt == "Encounter" and (not res.get("status") or not res.get("subject")):
            errors.append(f"entry[{idx}] Encounter missing status/subject")
        elif rt == "QuestionnaireResponse" and (not res.get("status") or not res.get("subject")):
            errors.append(f"entry[{idx}] QuestionnaireResponse missing status/subject")
        elif rt == "Provenance" and (not res.get("target") or not res.get("recorded")):
            errors.append(f"entry[{idx}] Provenance missing target/recorded")
    return errors


@router.get("/export/fhir")
async def export_fhir(cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """FHIR R4 collection Bundle (Patient, Encounter, Condition, MedicationStatement, MedicationRequest,
    AllergyIntolerance, Observation, QuestionnaireResponse, Provenance) — NRCeS / ABDM R4 aligned."""
    from app.models import ClinicalSymptom, Encounter, FollowUpQuestion

    pid = cu.id
    entries: list[dict] = []

    def add(resource: dict) -> str:
        rid = resource.setdefault("id", str(uuid.uuid4()))
        entries.append({"fullUrl": f"urn:uuid:{rid}", "resource": resource})
        return rid

    p = await db.get(PatientProfile, pid)
    patient = {"resourceType": "Patient", "id": pid}
    if p:
        if pname := cu.cipher.dec(p.name_enc):
            patient["name"] = [{"text": pname}]
        if dob := cu.cipher.dec(p.dob_enc):
            patient["birthDate"] = dob
        if p.sex in ("male", "female"):
            patient["gender"] = p.sex
        elif p.sex:
            patient["gender"] = "other"
    add(patient)
    subject = {"reference": f"urn:uuid:{pid}"}

    def add_provenance(target_id: str, source: str, verification: str, recorded_at: datetime) -> None:
        add({
            "resourceType": "Provenance",
            "target": [{"reference": f"urn:uuid:{target_id}"}],
            "recorded": recorded_at.isoformat() + "Z",
            "activity": {"text": f"source={source}; verification={verification}"},
            "agent": [{"who": {"display": source}}],
        })

    for enc in await db.scalars(select(Encounter).where(Encounter.user_id == pid)):
        enc_res = {
            "resourceType": "Encounter",
            "id": enc.id,
            "status": "finished" if enc.status in ("completed", "closed") else "in-progress",
            "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                      "code": "VR" if enc.channel == "voice" else "AMB"},
            "subject": subject,
            "period": {"start": enc.started_at.isoformat() + "Z",
                       **({"end": enc.ended_at.isoformat() + "Z"} if enc.ended_at else {})},
        }
        if cc := cu.cipher.dec(enc.chief_complaint_enc):
            enc_res["reasonCode"] = [{"text": cc}]
        eid = add(enc_res)
        add_provenance(eid, f"encounter:{enc.channel}", "patient_reported", enc.started_at)

    for a in await db.scalars(select(Allergy).where(Allergy.user_id == pid)):
        res = {"resourceType": "AllergyIntolerance", "patient": subject,
               "code": {"text": cu.cipher.dec(a.substance_enc)},
               "category": [{"drug": "medication", "food": "food"}.get(a.type, "environment")]}
        if a.severity in ("mild", "moderate", "severe"):
            res["reaction"] = [{"severity": a.severity,
                                "manifestation": [{"text": cu.cipher.dec(a.reaction_enc) or "reaction"}]}]
        add(res)

    for c in await db.scalars(select(Condition).where(Condition.user_id == pid)):
        coding = []
        if c.snomed_code:
            coding.append({"system": "http://snomed.info/sct", "code": c.snomed_code})
        if c.icd10:
            coding.append({"system": "http://hl7.org/fhir/sid/icd-10", "code": c.icd10})
        add({"resourceType": "Condition", "subject": subject,
             "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                                            "code": "resolved" if c.status == "resolved" else "active"}]},
             "verificationStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-ver-status",
                                                "code": "confirmed" if getattr(c, "verification_status", "") == "clinician_confirmed" else "unconfirmed"}]},
             "code": {"text": cu.cipher.dec(c.display_enc), **({"coding": coding} if coding else {})}})

    for sx in await db.scalars(select(ClinicalSymptom).where(ClinicalSymptom.user_id == pid)):
        ver_code = "confirmed" if sx.verification_status == "clinician_confirmed" else "unconfirmed"
        clin_code = "resolved" if sx.status == "resolved" else "active"
        cond_res = {
            "resourceType": "Condition",
            "id": sx.id,
            "subject": subject,
            "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": clin_code}]},
            "verificationStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-ver-status", "code": ver_code}]},
            "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-category", "code": "problem-list-item"}]}],
            "code": {"text": sx.normalized_concept or sx.name},
        }
        if sx.estimated_onset:
            cond_res["onsetDateTime"] = sx.estimated_onset.isoformat() + "Z"
        if sx.encounter_id:
            cond_res["encounter"] = {"reference": f"urn:uuid:{sx.encounter_id}"}
        sid = add(cond_res)
        add_provenance(sid, sx.source, sx.verification_status, sx.first_reported_at)

    for m in await db.scalars(select(Medication).where(Medication.user_id == pid)):
        mname = cu.cipher.dec(m.name_enc)
        res = {"resourceType": "MedicationStatement", "subject": subject,
               "status": {"active": "active", "stopped": "stopped", "completed": "completed"}.get(m.status, "unknown"),
               "medicationCodeableConcept": {"text": mname}}
        if m.start_date or m.end_date:
            res["effectivePeriod"] = {k: v.isoformat() for k, v in
                                      (("start", m.start_date), ("end", m.end_date)) if v}
        if m.frequency and m.frequency.get("label"):
            res["dosage"] = [{"text": m.frequency["label"] + (f", {m.timing.replace('_', ' ')}" if m.timing else "")}]
        add(res)
        if getattr(m, "prescribed_or_self", "unknown") == "prescribed":
            add({
                "resourceType": "MedicationRequest",
                "status": "active" if m.status == "active" else "completed",
                "intent": "order",
                "subject": subject,
                "medicationCodeableConcept": {"text": mname},
            })

    for v in await db.scalars(select(Vital).where(Vital.user_id == pid)):
        code, display = VITAL_LOINC.get(v.type, (None, v.type))
        obs = {"resourceType": "Observation", "status": "preliminary" if getattr(v, "is_estimate", False) else "final",
               "subject": subject,
               "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category",
                                         "code": "laboratory" if v.type.startswith("glucose") else "vital-signs"}]}],
               "code": {"coding": [{"system": "http://loinc.org", "code": code, "display": display}]} if code
               else {"text": display},
               "effectiveDateTime": v.measured_at.isoformat() + "Z",
               "valueQuantity": {"value": v.value, "unit": v.unit, "system": "http://unitsofmeasure.org",
                                 "code": UCUM.get(v.unit, v.unit)}}
        if getattr(v, "is_estimate", False):
            obs["note"] = [{"text": "Patient-estimated value (not device-measured)"}]
        oid = add(obs)
        if getattr(v, "is_estimate", False) or v.source in ("patient_estimate", "patient_report"):
            add_provenance(oid, v.source, "unverified" if getattr(v, "is_estimate", False) else "patient_reported", v.measured_at)

    for lr in await db.scalars(select(LabResult).where(LabResult.user_id == pid)):
        obs = {"resourceType": "Observation", "status": "final", "subject": subject,
               "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category",
                                         "code": "laboratory"}]}],
               "code": {"coding": [{"system": "http://loinc.org", "code": lr.loinc_code}], "text": lr.test_name}
               if lr.loinc_code else {"text": lr.test_name},
               "effectiveDateTime": lr.observed_at.isoformat() + "Z"}
        if lr.value is not None:
            obs["valueQuantity"] = {"value": lr.value, "unit": lr.unit or ""}
        else:
            obs["valueString"] = lr.value_text or ""
        if lr.ref_low is not None or lr.ref_high is not None:
            obs["referenceRange"] = [{k: {"value": v, "unit": lr.unit or ""} for k, v in
                                      (("low", lr.ref_low), ("high", lr.ref_high)) if v is not None}]
        if lr.flag in ("H", "L", "N"):
            obs["interpretation"] = [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation",
                                                  "code": lr.flag}]}]
        add(obs)

    # Answered follow-up questions mapped to QuestionnaireResponse
    answered_fqs = list(await db.scalars(select(FollowUpQuestion).where(
        FollowUpQuestion.user_id == pid, FollowUpQuestion.status == "answered")))
    if answered_fqs:
        by_enc: dict[str, list[FollowUpQuestion]] = {}
        for fq in answered_fqs:
            by_enc.setdefault(fq.encounter_id, []).append(fq)
        for enc_id, fqs in by_enc.items():
            add({
                "resourceType": "QuestionnaireResponse",
                "status": "completed",
                "subject": subject,
                "encounter": {"reference": f"urn:uuid:{enc_id}"},
                "authored": (fqs[-1].answered_at or utcnow()).isoformat() + "Z",
                "item": [
                    {
                        "linkId": fq.question_key,
                        "text": cu.cipher.dec(fq.question_text_enc) or fq.question_key,
                        "answer": [{"valueString": cu.cipher.dec(fq.answer_text_enc) or ""}],
                    }
                    for fq in fqs
                ],
            })

    bundle = {"resourceType": "Bundle", "type": "collection", "timestamp": utcnow().isoformat() + "Z",
              "entry": entries}
    errs = validate_fhir_bundle(bundle)
    if errs:
        raise ValueError(f"FHIR bundle validation failed: {errs}")
    return Response(json.dumps(bundle, ensure_ascii=False, default=str), media_type="application/fhir+json",
                    headers={"Content-Disposition": 'attachment; filename="sushrutai-fhir-bundle.json"'})


# ---------------------------------------------------------------- month calendar (desktop dashboard)
@router.get("/calendar")
async def calendar(month: str | None = None, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """One month of the person's care, day by day: dose adherence, vaccines / check-ups (care schedule), "check in
    with me" follow-ups, medicines running out, and report days. month = YYYY-MM (default: this month)."""
    from fastapi import HTTPException

    from app.models import FollowUp, LabResult
    from app.routers.care import schedule_for

    today = local_today()
    try:
        first = date.fromisoformat(f"{month}-01") if month else today.replace(day=1)
    except ValueError:
        raise HTTPException(422, "month must be YYYY-MM") from None
    nxt = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    days: dict[str, dict] = {}

    def day(d: date) -> dict:
        return days.setdefault(d.isoformat(), {"taken": 0, "skipped": 0, "events": []})

    for log in await db.scalars(select(DoseLog).where(DoseLog.user_id == cu.id, DoseLog.day >= first, DoseLog.day < nxt)):
        day(log.day)["taken" if log.status == "taken" else "skipped"] += 1
    for item in (await schedule_for(db, cu))["items"]:
        when = item["due_from"] if item["due_from"] >= today else today
        if first <= when < nxt:
            day(when)["events"].append({"type": "care", "title": item["title"], "status": item["status"], "href": "/me/care"})
    for f in await db.scalars(select(FollowUp).where(FollowUp.user_id == cu.id, FollowUp.due_day >= first, FollowUp.due_day < nxt)):
        day(f.due_day)["events"].append({"type": "follow_up", "title": cu.cipher.dec(f.text_enc), "status": "due", "href": "/ask"})
    for m in await _active_meds(db, cu):
        if m["end_date"] and first <= m["end_date"] < nxt:
            day(m["end_date"])["events"].append({"type": "refill", "title": m["name"], "status": "soon", "href": "/medicines"})
    seen: set[tuple[str, str]] = set()
    for lr in await db.scalars(select(LabResult).where(LabResult.user_id == cu.id)):
        d = lr.observed_at.date()
        if first <= d < nxt and (d.isoformat(), lr.document_id or "") not in seen:
            seen.add((d.isoformat(), lr.document_id or ""))
            day(d)["events"].append({"type": "report", "title": lr.test_name, "status": "done", "href": "/records?tab=library"})
    from app.models import CalendarEvent

    for ce in await db.scalars(select(CalendarEvent).where(CalendarEvent.user_id == cu.id, CalendarEvent.day >= first,
                                                           CalendarEvent.day < nxt)):
        day(ce.day)["events"].append({"type": "mine", "id": ce.id, "kind": ce.kind, "time": ce.time,
                                      "title": cu.cipher.dec(ce.title_enc), "status": "planned", "href": ""})
    for d in days.values():  # timed ones first, then the rest in the order they were added
        d["events"].sort(key=lambda e: (e.get("time") or "99:99") if e["type"] == "mine" else "99:99")
    return {"month": first.strftime("%Y-%m"), "today": today, "days": days}


class CalendarEventIn(BaseModel):
    day: date
    title: str = Field(min_length=1, max_length=120)
    kind: Literal["appointment", "reminder", "note"] = "appointment"
    time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


@router.post("/calendar/events", status_code=201)
async def add_calendar_event(body: CalendarEventIn, cu: CurrentUser = Depends(current_user),
                             db: AsyncSession = Depends(get_db)):
    from app.models import CalendarEvent

    ev = CalendarEvent(user_id=cu.id, day=body.day, kind=body.kind, time=body.time,
                       title_enc=cu.cipher.enc(body.title.strip()))
    db.add(ev)
    await db.commit()
    return {"id": ev.id, "day": ev.day, "kind": ev.kind, "time": ev.time, "title": body.title.strip()}


@router.delete("/calendar/events/{eid}", status_code=204)
async def delete_calendar_event(eid: str, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    from fastapi import HTTPException

    from app.models import CalendarEvent

    ev = await db.get(CalendarEvent, eid)
    if ev is None or ev.user_id != cu.id:
        raise HTTPException(404, "Not found")
    await db.delete(ev)
    await db.commit()
