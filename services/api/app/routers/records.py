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
    model_pref: Literal["auto", "balanced", "fast", "instant", "cloud"] | None = None


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
    return sorted(ev, key=lambda e: e["at"], reverse=True)


VITAL_LOINC = {"bp_sys": ("8480-6", "Systolic blood pressure"), "bp_dia": ("8462-4", "Diastolic blood pressure"),
               "hr": ("8867-4", "Heart rate"), "spo2": ("59408-5", "Oxygen saturation by pulse oximetry"),
               "temp": ("8310-5", "Body temperature"), "rr": ("9279-1", "Respiratory rate"),
               "weight": ("29463-7", "Body weight"), "waist": ("8280-0", "Waist circumference"),
               "glucose_f": ("1558-6", "Fasting glucose"), "glucose_pp": ("1521-4", "Glucose 2 h post meal"),
               "glucose_r": ("2345-7", "Glucose")}
UCUM = {"mmHg": "mm[Hg]", "bpm": "/min", "%": "%", "°C": "Cel", "/min": "/min", "kg": "kg", "cm": "cm",
        "mg/dL": "mg/dL"}


@router.get("/export/fhir")
async def export_fhir(cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """FHIR R4 collection Bundle (Patient, AllergyIntolerance, Condition, MedicationStatement, Observation) —
    the format ABDM health records use (NRCeS FHIR profiles build on R4)."""
    pid = cu.id
    entries: list[dict] = []

    def add(resource: dict) -> None:
        rid = resource.setdefault("id", str(uuid.uuid4()))
        entries.append({"fullUrl": f"urn:uuid:{rid}", "resource": resource})

    p = await db.get(PatientProfile, pid)
    patient = {"resourceType": "Patient", "id": pid}
    if p:
        if dob := cu.cipher.dec(p.dob_enc):
            patient["birthDate"] = dob
        if p.sex in ("male", "female"):
            patient["gender"] = p.sex
        elif p.sex:
            patient["gender"] = "other"
    add(patient)
    subject = {"reference": f"urn:uuid:{pid}"}
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
             "code": {"text": cu.cipher.dec(c.display_enc), **({"coding": coding} if coding else {})}})
    for m in await db.scalars(select(Medication).where(Medication.user_id == pid)):
        res = {"resourceType": "MedicationStatement", "subject": subject,
               "status": {"active": "active", "stopped": "stopped", "completed": "completed"}.get(m.status, "unknown"),
               "medicationCodeableConcept": {"text": cu.cipher.dec(m.name_enc)}}
        if m.start_date or m.end_date:
            res["effectivePeriod"] = {k: v.isoformat() for k, v in
                                      (("start", m.start_date), ("end", m.end_date)) if v}
        if m.frequency and m.frequency.get("label"):
            res["dosage"] = [{"text": m.frequency["label"] + (f", {m.timing.replace('_', ' ')}" if m.timing else "")}]
        add(res)
    for v in await db.scalars(select(Vital).where(Vital.user_id == pid)):
        code, display = VITAL_LOINC.get(v.type, (None, v.type))
        add({"resourceType": "Observation", "status": "final", "subject": subject,
             "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category",
                                       "code": "laboratory" if v.type.startswith("glucose") else "vital-signs"}]}],
             "code": {"coding": [{"system": "http://loinc.org", "code": code, "display": display}]} if code
             else {"text": display},
             "effectiveDateTime": v.measured_at.isoformat() + "Z",
             "valueQuantity": {"value": v.value, "unit": v.unit, "system": "http://unitsofmeasure.org",
                               "code": UCUM.get(v.unit, v.unit)}})
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
    bundle = {"resourceType": "Bundle", "type": "collection", "timestamp": utcnow().isoformat() + "Z",
              "entry": entries}
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
    return {"month": first.strftime("%Y-%m"), "today": today, "days": days}
