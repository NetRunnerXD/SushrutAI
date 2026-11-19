"""One search box for the whole app: medicines catalogue + the active profile's own records.

Personal records are encrypted per user, so they are decrypted and matched in memory (a person has hundreds of
rows at most). Everything is scoped to the active profile (X-Profile-Id), like every other endpoint.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.medicine import service as medicine_service
from app.models import Allergy, ChatSession, Condition, LabResult, Medication, Message
from app.security.auth import CurrentUser, current_user

router = APIRouter(tags=["search"])


@router.get("/search/topic")
async def topic(q: str = Query(min_length=2, max_length=80), db: AsyncSession = Depends(get_db)):
    """'Learn more' about a condition: the best patient-education page in the trusted knowledge base (MedlinePlus,
    WHO, CDC, NHS, Indian guidelines). Retrieval only, no model. None when nothing is relevant enough."""
    from app.rag.store import search as kb_search

    hits = await kb_search(db, q, collections=["patient_ed", "guidelines_in", "nutrition_in"], k=3, rerank_top=6)
    best = next((h for h in hits if h.source_url), None)
    if not best or best.score < 0.02:
        return None
    return {"title": best.title, "url": best.source_url, "source": best.source_id.split(":", 1)[0],
            "snippet": " ".join(best.content.split()[:45]) + "…"}


def _hit(text: str | None, q: str) -> bool:
    return bool(text) and q in text.lower()


@router.get("/search")
async def search(q: str = Query(min_length=2, max_length=80), cu: CurrentUser = Depends(current_user),
                 db: AsyncSession = Depends(get_db)):
    ql = q.strip().lower()
    c = cu.cipher
    meds = [{"id": m.id, "name": c.dec(m.name_enc), "status": m.status}
            for m in await db.scalars(select(Medication).where(Medication.user_id == cu.id))]
    labs: dict[str, dict] = {}
    for lr in await db.scalars(select(LabResult).where(LabResult.user_id == cu.id).order_by(LabResult.observed_at)):
        if _hit(lr.test_name, ql):
            labs[lr.test_name.lower()] = {"id": lr.id, "name": lr.test_name, "value": lr.value_text or lr.value,
                                          "unit": lr.unit, "flag": lr.flag, "at": lr.observed_at}
    conditions = [c.dec(x.display_enc) for x in await db.scalars(select(Condition).where(Condition.user_id == cu.id))]
    allergies = [c.dec(x.substance_enc) for x in await db.scalars(select(Allergy).where(Allergy.user_id == cu.id))]
    chats = []
    # Questions AND answers in past chats (newest 60 chats; messages are encrypted, so matched after decrypting)
    sessions = list(await db.scalars(select(ChatSession).where(ChatSession.user_id == cu.id)
                                     .order_by(ChatSession.started_at.desc()).limit(60)))
    for s in sessions:
        msgs = list(await db.scalars(select(Message).where(Message.session_id == s.id).order_by(Message.created_at)))
        if not msgs:
            continue
        title = next((c.dec(m.content_enc) for m in msgs if m.role == "user"), "")
        for m in msgs:
            body = c.dec(m.content_enc) or ""
            i = body.lower().find(ql)
            if i >= 0:
                snippet = body[max(0, i - 40): i + len(ql) + 60].replace("\n", " ").strip()
                chats.append({"id": s.id, "title": title[:80], "at": s.started_at, "in": m.role,
                              "snippet": ("…" if i > 40 else "") + snippet + "…"})
                break
        if len(chats) >= 6:
            break
    catalogue = [{"id": p["id"], "name": p["name"], "is_janaushadhi": p["is_janaushadhi"],
                  "per_unit_inr": (p["price"] or {}).get("per_unit_inr")}
                 for p in await medicine_service.search(db, q, limit=6) if p.get("match_score", 0) >= 70]
    return {
        "my_medicines": [m for m in meds if _hit(m["name"], ql)][:5],
        "labs": list(labs.values())[-5:],
        "conditions": [x for x in conditions if _hit(x, ql)][:5],
        "allergies": [x for x in allergies if _hit(x, ql)][:5],
        "chats": chats,
        "medicines": catalogue,
    }
