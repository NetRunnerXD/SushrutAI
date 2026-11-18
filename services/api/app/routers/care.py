"""Vaccines & check-ups for the active profile: India's child immunisation schedule (from date of birth) and the
pregnancy check-up schedule (from the first day of the last period). Only 'done' marks and the LMP are stored."""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.care import schedules as S
from app.clock import local_today
from app.db import get_db
from app.models import CareRecord, PatientProfile, Pregnancy
from app.models import _now as utcnow
from app.security.auth import CurrentUser, current_user

router = APIRouter(prefix="/me", tags=["care"])
CHILD_UNTIL_YEARS = 17


class DoneIn(BaseModel):
    done_on: date | None = None


class PregnancyIn(BaseModel):
    lmp: date | None  # null = no longer pregnant (stops the check-up schedule)


async def _done(db: AsyncSession, cu: CurrentUser) -> dict[str, date]:
    return {r.item_code: r.done_on for r in await db.scalars(select(CareRecord).where(CareRecord.user_id == cu.id))}


async def schedule_for(db: AsyncSession, cu: CurrentUser) -> dict:
    today = local_today()
    done = await _done(db, cu)
    p = await db.get(PatientProfile, cu.id)
    dob = cu.cipher.dec(p.dob_enc) if p and p.dob_enc else None
    child = None
    if dob:
        try:
            d = date.fromisoformat(dob)
            if (today - d).days < CHILD_UNTIL_YEARS * 365.25:
                child = {"dob": d, "items": S.child_schedule(d, today, done), "source": S.NIS_SOURCE, "url": S.NIS_URL}
        except ValueError:
            pass
    preg = None
    pr = await db.get(Pregnancy, cu.id)
    if pr and pr.active:
        lmp = date.fromisoformat(cu.cipher.dec(pr.lmp_enc))
        weeks = (today - lmp).days // 7
        if weeks <= 44:
            preg = {"lmp": lmp, "weeks": weeks, "due_date": lmp + timedelta(days=280),
                    "items": S.pregnancy_schedule(lmp, today, done), "source": S.ANC_SOURCE, "url": S.ANC_URL}
    items = (child["items"] if child else []) + (preg["items"] if preg else [])
    return {"child": child, "pregnancy": preg, "has_dob": bool(dob),
            "items": [i for i in items if i["status"] in ("due", "overdue", "soon")]}


@router.get("/care-schedule")
async def get_schedule(cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    return await schedule_for(db, cu)


@router.post("/care-schedule/{code}")
async def mark_done(code: str, body: DoneIn, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    if code not in {i.code for i in S.CHILD + S.PREGNANCY}:
        raise HTTPException(404, "Unknown schedule item")
    row = (await db.scalars(select(CareRecord).where(CareRecord.user_id == cu.id, CareRecord.item_code == code))).first()
    if row is None:
        db.add(CareRecord(user_id=cu.id, item_code=code, done_on=body.done_on or local_today()))
    else:
        row.done_on = body.done_on or row.done_on
    await db.commit()
    return await schedule_for(db, cu)


@router.delete("/care-schedule/{code}")
async def undo(code: str, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await db.execute(delete(CareRecord).where(CareRecord.user_id == cu.id, CareRecord.item_code == code))
    await db.commit()
    return await schedule_for(db, cu)


@router.put("/pregnancy")
async def set_pregnancy(body: PregnancyIn, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    today = local_today()
    pr = await db.get(Pregnancy, cu.id)
    if body.lmp is None:
        if pr:
            pr.active = False
    else:
        if body.lmp > today or (today - body.lmp).days > 44 * 7:
            raise HTTPException(422, "The first day of the last period must be within the last 44 weeks")
        if pr is None:
            pr = Pregnancy(user_id=cu.id, lmp_enc=cu.cipher.enc(body.lmp.isoformat()))
            db.add(pr)
        pr.lmp_enc, pr.active, pr.updated_at = cu.cipher.enc(body.lmp.isoformat()), True, utcnow()
    await db.commit()
    return await schedule_for(db, cu)


def care_ics_events(schedule: dict, today: date, horizon_days: int = 365) -> list[str]:
    """All-day calendar events for care items that are due or coming up (for the reminders .ics)."""
    lines = []
    stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
    for part in (schedule.get("child"), schedule.get("pregnancy")):
        for i in (part or {}).get("items", []):
            if i["status"] == "done" or i["due_by"] < today or (i["due_from"] - today).days > horizon_days:
                continue
            day = max(i["due_from"], today)
            lines += ["BEGIN:VEVENT", f"UID:care-{i['code']}@sushrutai", f"DTSTAMP:{stamp}",
                      f"DTSTART;VALUE=DATE:{day:%Y%m%d}", f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}",
                      f"SUMMARY:{i['title']} due", "END:VEVENT"]
    return lines
