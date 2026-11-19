"""Doctor visit summary: one page a patient (or caregiver) can print, save as PDF or show on the phone.

Everything is assembled deterministically from the patient's own records — no AI-generated text — so it is safe
to hand to a clinician. The web app renders it as a print-optimised page; the browser's "Save as PDF" handles
every Indian script correctly, which server-side PDF libraries do not.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clinical.bmi import bmi, bmi_category
from app.clock import local_today
from app.db import get_db
from app.models import (
    Allergy, ChatSession, CheckIn, Condition, HouseholdMember, LabResult, Medication, Message, PatientProfile, TriageSession,
    Vital, VisitQuestion,
)
from app.models import _now as utcnow
from app.security.auth import CurrentUser, current_user
from app.triage import engine
from app.triage.store import load_state

router = APIRouter(prefix="/me", tags=["visit"])
URGENCY_ORDER = {"emergency": 0, "see_doctor_24h": 1, "see_doctor_week": 2, "self_care": 3}

VITAL_LABEL = {"bp_sys": "BP systolic", "bp_dia": "BP diastolic", "hr": "Pulse", "spo2": "SpO2", "temp": "Temperature",
               "rr": "Breathing rate", "glucose_f": "Fasting sugar", "glucose_pp": "Sugar after meal",
               "glucose_r": "Random sugar", "weight": "Weight", "waist": "Waist"}


def _age(dob: str | None) -> int | None:
    try:
        return int((local_today() - date.fromisoformat(dob)).days // 365.25) if dob else None
    except ValueError:
        return None


def questions_to_ask(*, abnormal: list[dict], meds: list[dict], checks: list[dict], allergies: list[str],
                     pregnancy: str | None) -> list[str]:
    """Plain, patient-voiced questions chosen by simple rules from the record."""
    q: list[str] = []
    for lab in abnormal[:3]:
        word = "high" if lab["flag"] == "H" else "low"
        q.append(f"My {lab['test']} is {word} ({lab['value']} {lab['unit'] or ''}). What does this mean and what should I do?".replace(" )", ")"))
    if len(meds) >= 5:
        q.append(f"I take {len(meds)} medicines. Are all of them still needed? Do any of them interact?")
    elif meds:
        q.append("Should I continue all my current medicines, and for how long?")
    if checks:
        q.append(f"About my {checks[0]['complaint'][:60]}: which tests do I need, and which warning signs mean I should come back?")
    if meds and allergies:
        q.append("Are any of my medicines a problem with my allergies?")
    if pregnancy in ("pregnant", "possibly_pregnant"):
        q.append("Are my medicines safe in pregnancy?")
    q.append("When should I come for a follow-up visit?")
    return q[:6]


@router.get("/visit-summary")
async def visit_summary(days: int = 90, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    days = max(7, min(days, 365))
    since = utcnow() - timedelta(days=days)
    c = cu.cipher
    p = await db.get(PatientProfile, cu.id)
    dob = c.dec(p.dob_enc) if p and p.dob_enc else None

    name = None
    if cu.is_member:
        link = (await db.scalars(select(HouseholdMember).where(HouseholdMember.user_id == cu.account_id,
                                                               HouseholdMember.member_id == cu.id))).first()
        name = cu.account_cipher.dec(link.name_enc) if link else None

    vitals = list(await db.scalars(select(Vital).where(Vital.user_id == cu.id).order_by(Vital.measured_at.desc())))
    latest: dict[str, Vital] = {}
    for v in vitals:
        latest.setdefault(v.type, v)
    weight = latest["weight"].value if "weight" in latest else None
    b = bmi(weight, p.height_cm) if (weight and p and p.height_cm) else None
    bp = [{"sys": s.value, "dia": d.value, "at": s.measured_at} for s in vitals if s.type == "bp_sys"
          for d in vitals if d.type == "bp_dia" and abs((d.measured_at - s.measured_at).total_seconds()) < 120][:6]

    today = local_today()
    meds = [{"name": c.dec(m.name_enc), "dose": m.dose, "frequency": m.frequency, "timing": m.timing,
             "since": m.start_date, "until": m.end_date}
            for m in await db.scalars(select(Medication).where(Medication.user_id == cu.id, Medication.status == "active"))
            if not (m.end_date and m.end_date < today)]
    allergies = [c.dec(a.substance_enc) for a in await db.scalars(select(Allergy).where(Allergy.user_id == cu.id))]
    conditions = [c.dec(x.display_enc) for x in await db.scalars(
        select(Condition).where(Condition.user_id == cu.id, Condition.status == "active"))]

    labs_latest: dict[str, LabResult] = {}
    for lr in await db.scalars(select(LabResult).where(LabResult.user_id == cu.id, LabResult.observed_at >= utcnow() - timedelta(days=365))
                               .order_by(LabResult.observed_at.desc())):
        labs_latest.setdefault(lr.test_name.lower(), lr)
    abnormal = [{"test": lr.test_name, "value": lr.value_text or (f"{lr.value:g}" if lr.value is not None else "—"),
                 "unit": lr.unit, "flag": lr.flag, "range": (f"{lr.ref_low:g}–{lr.ref_high:g}" if lr.ref_low is not None
                                                             and lr.ref_high is not None else None),
                 "at": lr.observed_at} for lr in labs_latest.values() if lr.flag in ("H", "L")]

    checks = []
    for ts in await db.scalars(select(TriageSession).where(TriageSession.user_id == cu.id, TriageSession.created_at >= since)
                               .order_by(TriageSession.created_at.desc())):
        st = load_state(ts, c)
        if not st.get("complaint"):
            continue
        ranked = engine.summary(st)[:3] if st.get("conditions") and st.get("posterior") else []
        checks.append({"at": ts.created_at, "complaint": st["complaint"],
                       "possible": [r["name"] for r in ranked],
                       "urgency": min((r["urgency"] for r in ranked), key=lambda u: URGENCY_ORDER.get(u, 9))
                                  if ranked else None,
                       "yes_answers": [f["question"] for f in st.get("findings", []) if st.get("answers", {}).get(f["id"]) == "yes"][:6]})
        if len(checks) >= 5:
            break

    topics = []
    for s in await db.scalars(select(ChatSession).where(ChatSession.user_id == cu.id, ChatSession.started_at >= since)
                              .order_by(ChatSession.started_at.desc())):
        first = (await db.scalars(select(Message).where(Message.session_id == s.id, Message.role == "user")
                                  .order_by(Message.created_at))).first()
        if first:
            topics.append({"at": s.started_at, "text": c.dec(first.content_enc)[:140]})
        if len(topics) >= 5:
            break

    moods = list(await db.scalars(select(CheckIn).where(CheckIn.user_id == cu.id, CheckIn.day >= since.date())
                                  .order_by(CheckIn.day)))
    checkins = {"good": sum(m.mood == "good" for m in moods), "okay": sum(m.mood == "okay" for m in moods),
                "unwell": sum(m.mood == "unwell" for m in moods),
                "unwell_days": [m.day for m in moods if m.mood == "unwell"][-10:]}

    return {
        "generated_at": utcnow(), "period_days": days, "checkins": checkins,
        "patient": {"name": name, "age": _age(dob), "sex": p.sex if p else None,
                    "blood_group": p.blood_group if p else None, "height_cm": p.height_cm if p else None,
                    "weight_kg": weight, "bmi": round(b, 1) if b else None,
                    "bmi_category": bmi_category(b) if b else None,
                    "pregnancy_status": p.pregnancy_status if p else None,
                    "smoking_status": p.smoking_status if p else None,
                    "tobacco_chewing": p.tobacco_chewing if p else None,
                    "family_history": json.loads(c.dec(p.family_history_enc)) if p and p.family_history_enc else []},
        "allergies": allergies, "conditions": conditions, "medicines": meds,
        "abnormal_labs": abnormal,
        "vitals": [{"type": k, "label": VITAL_LABEL.get(k, k), "value": v.value, "unit": v.unit, "at": v.measured_at}
                   for k, v in latest.items()],
        "bp_readings": bp,
        "symptom_checks": checks, "questions_asked": topics,
        "my_questions": [c.dec(q.text_enc) for q in await db.scalars(
            select(VisitQuestion).where(VisitQuestion.user_id == cu.id).order_by(VisitQuestion.created_at))],
        "questions_to_ask": questions_to_ask(abnormal=abnormal, meds=meds, checks=checks, allergies=allergies,
                                             pregnancy=p.pregnancy_status if p else None),
        "note": "Prepared by the patient using SushrutAI from their own records. Not a medical document; "
                "please verify with the patient.",
    }
