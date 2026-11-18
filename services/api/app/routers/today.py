"""Cross-page helpers: questions for the next doctor visit, cheaper refills, and a household 'today' view."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import local_now
from app.db import get_db, scope_to
from app.medicine.checks import cheapest_option
from app.models import FollowUp, HouseholdMember, Medication, User, VisitQuestion
from app.routers import care as care_router
from app.routers import records as records_router
from app.security.auth import CurrentUser, current_user
from app.security.crypto import Cipher

router = APIRouter(prefix="/me", tags=["today"])
MAX_QUESTIONS = 30


# ---------------------------------------------------------------- questions for the doctor
class QuestionIn(BaseModel):
    text: str = Field(min_length=3, max_length=300)
    source: str = Field("manual", pattern="^(chat|manual)$")


async def list_questions(db: AsyncSession, cu: CurrentUser) -> list[dict]:
    rows = await db.scalars(select(VisitQuestion).where(VisitQuestion.user_id == cu.id).order_by(VisitQuestion.created_at))
    return [{"id": q.id, "text": cu.cipher.dec(q.text_enc), "source": q.source, "created_at": q.created_at} for q in rows]


@router.get("/visit-questions")
async def get_questions(cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    return await list_questions(db, cu)


@router.post("/visit-questions", status_code=201)
async def add_question(body: QuestionIn, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    existing = await list_questions(db, cu)
    text = body.text.strip()
    if any(q["text"].lower() == text.lower() for q in existing):
        return existing  # already saved
    if len(existing) >= MAX_QUESTIONS:
        raise HTTPException(400, f"You can keep up to {MAX_QUESTIONS} questions — remove ones you have asked")
    db.add(VisitQuestion(user_id=cu.id, text_enc=cu.cipher.enc(text), source=body.source))
    await db.commit()
    return await list_questions(db, cu)


@router.delete("/visit-questions/{qid}")
async def remove_question(qid: str, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await db.execute(delete(VisitQuestion).where(VisitQuestion.id == qid, VisitQuestion.user_id == cu.id))
    await db.commit()
    return await list_questions(db, cu)


# ---------------------------------------------------------------- follow-ups ("check in with me")
MAX_FOLLOW_UPS = 20


class FollowUpIn(BaseModel):
    text: str = Field(min_length=3, max_length=200)
    in_days: int = Field(1, ge=0, le=30)


async def list_follow_ups(db: AsyncSession, cu: CurrentUser, due_only: bool = False) -> list[dict]:
    q = select(FollowUp).where(FollowUp.user_id == cu.id)
    if due_only:
        q = q.where(FollowUp.due_day <= local_now().date())
    rows = await db.scalars(q.order_by(FollowUp.due_day, FollowUp.created_at))
    return [{"id": f.id, "text": cu.cipher.dec(f.text_enc), "due_day": f.due_day} for f in rows]


@router.get("/follow-ups")
async def get_follow_ups(due: bool = False, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    return await list_follow_ups(db, cu, due_only=due)


@router.post("/follow-ups", status_code=201)
async def add_follow_up(body: FollowUpIn, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    from datetime import timedelta

    existing = await list_follow_ups(db, cu)
    text = body.text.strip()
    if any(f["text"].lower() == text.lower() for f in existing):
        return existing
    if len(existing) >= MAX_FOLLOW_UPS:
        raise HTTPException(400, f"You can keep up to {MAX_FOLLOW_UPS} check-ins — remove old ones")
    db.add(FollowUp(user_id=cu.id, text_enc=cu.cipher.enc(text), due_day=local_now().date() + timedelta(days=body.in_days)))
    await db.commit()
    return await list_follow_ups(db, cu)


@router.delete("/follow-ups/{fid}")
async def remove_follow_up(fid: str, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    await db.execute(delete(FollowUp).where(FollowUp.id == fid, FollowUp.user_id == cu.id))
    await db.commit()
    return await list_follow_ups(db, cu)


# ---------------------------------------------------------------- refills
@router.get("/refills")
async def refills(days: int = 3, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Medicines running out within `days`, each with the cheapest same-composition option if one is known."""
    today = records_router.local_today()
    out = []
    for m in await db.scalars(select(Medication).where(Medication.user_id == cu.id, Medication.status == "active")):
        if not m.end_date:
            continue
        left = (m.end_date - today).days
        if 0 <= left <= days:
            name = cu.cipher.dec(m.name_enc)
            out.append({"medication_id": m.id, "name": name, "days_left": left,
                        "cheapest": await cheapest_option(db, name, m.product_id)})
    return sorted(out, key=lambda r: r["days_left"])


# ---------------------------------------------------------------- household today
async def _person_today(db: AsyncSession, who: CurrentUser) -> dict:
    d = await records_router.doses(day=None, cu=who, db=db)
    scheduled = [i for i in d["items"] if i["slot"] != "as_needed"]
    left = [i for i in scheduled if i["status"] not in ("taken", "skipped")]
    now = local_now().strftime("%H:%M")
    upcoming = [i for i in left if i["time"] and i["time"] >= now]
    care = await care_router.schedule_for(db, who)
    return {"doses_total": len(scheduled), "doses_left": len(left),
            # the next dose still to come today; times already passed count as missed, not "next"
            "next_dose": min(upcoming, key=lambda i: i["time"], default=None),
            "doses_missed": sum(1 for i in left if i["time"] and i["time"] < now),
            "care_due": [{"title": c["title"], "status": c["status"]} for c in care["items"]
                         if c["status"] in ("due", "overdue")]}


@router.get("/household-today")
async def household_today(cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """The account holder's day plus every family member they manage: doses left and vaccines/check-ups due."""
    aid = cu.account_id
    account = cu.account or cu.user
    me = CurrentUser(user=account, cipher=Cipher(account.dek_wrapped, account.id), account=account)
    await scope_to(db, aid)
    people = [{"id": None, "name": None, "relation": "self", **await _person_today(db, me)}]
    links = list(await db.scalars(select(HouseholdMember).where(HouseholdMember.user_id == aid)
                                  .order_by(HouseholdMember.created_at)))
    for link in links:
        member = await db.get(User, link.member_id)
        if member is None:
            continue
        await scope_to(db, member.id, aid)  # each person's rows are only visible in their own scope
        who = CurrentUser(user=member, cipher=Cipher(member.dek_wrapped, member.id), account=account)
        people.append({"id": member.id, "name": me.cipher.dec(link.name_enc), "relation": link.relation,
                       **await _person_today(db, who)})
    await scope_to(db, cu.id, aid)
    return {"people": people}
