"""The person's health memory: timeline, undo a saved value, patterns, a spoken summary of recent weeks."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.memory.extract import quick_facts
from app.memory.patterns import insights
from app.memory.store import save_facts, timeline_items, undo_fact
from app.models import Episode, HealthFact
from app.security.auth import CurrentUser, current_user

router = APIRouter(prefix="/me/memory", tags=["memory"])


@router.get("/timeline")
async def memory_timeline(cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    return await timeline_items(db, cu)


@router.delete("/facts/{fid}", status_code=204)
async def delete_fact(fid: str, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Undo a "Saving: …" chip (also removes the reading written with it)."""
    if not await undo_fact(db, cu, fid):
        raise HTTPException(404, "Not found")


class NoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)


@router.post("/note", status_code=201)
async def add_note(body: NoteIn, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Remember something the person said or typed outside a chat (values in it are read too)."""
    from app.memory.extract import Fact

    facts = quick_facts(body.text) + [Fact("note", "note", {"text": body.text.strip()})]
    return await save_facts(db, cu, facts, source="manual")


@router.get("/insights")
async def memory_insights(cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    return await insights(db, cu)


@router.get("/summary")
async def memory_summary(days: int = 7, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """A short spoken summary of the last `days` days (the optional weekly / monthly health summary)."""
    days = max(1, min(days, 92))
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    eps = list(await db.scalars(select(Episode).where(Episode.user_id == cu.id, Episode.last_activity_at >= since)))
    n_facts = await db.scalar(select(HealthFact.id).where(HealthFact.user_id == cu.id, HealthFact.observed_at >= since).limit(1))
    pats = await insights(db, cu)
    lines = []
    if eps:
        lines.append(f"In the last {days} days you talked about " + "; ".join(cu.cipher.dec(e.title_enc) for e in eps[:4]) + ".")
    for p in pats[:2]:
        lines.append(p["say"])
    if not lines:
        lines.append("Nothing new was noted in this period." if not n_facts else f"A few readings were saved in the last {days} days.")
    return {"days": days, "text": " ".join(lines), "episodes": len(eps), "patterns": pats}


class FeedbackIn(BaseModel):
    kind: str = Field(pattern=r"^(answer|medicine)$")
    name: str = Field(min_length=1, max_length=80)          # the answer's topic, or the medicine
    value: str = Field(pattern=r"^(up|down|better|same|worse|side_effect)$")
    note: str = Field(default="", max_length=200)


@router.post("/feedback", status_code=201)
async def feedback(body: FeedbackIn, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Light, skippable feedback ("Was this helpful?", "Did the medicine work?"). Stored only as high-level facts
    for this person: medicine responses come back in later storylines and answers ("last time it helped")."""
    from app.memory.extract import Fact

    if body.kind == "medicine":
        f = Fact("medicine_response", body.name.lower(), {"effect": body.value, "note": body.note})
    else:
        f = Fact("feedback", body.name.lower(), {"helpful": body.value == "up", "note": body.note})
    return await save_facts(db, cu, [f], source="manual", write_vitals=False)
