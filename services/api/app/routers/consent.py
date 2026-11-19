from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import Consent, ConsentEvent, _now as utcnow
from app.safety.disclaimer import DISCLAIMER_VERSION, disclaimer
from app.security.auth import CurrentUser, current_user
from app.security.consent import NOTICE_VERSION, PURPOSES, active_purposes

router = APIRouter(tags=["consent"])


@router.get("/privacy/notice")
async def notice(lang: str = "en"):
    lang = lang if lang in ("en", "hi") else "en"
    return {
        "notice_version": NOTICE_VERSION,
        "disclaimer_version": DISCLAIMER_VERSION,
        "disclaimer": disclaimer(lang),
        "data_fiduciary": "SushrutAI (self-hosted instance)",
        "grievance_contact": "Configure GRIEVANCE_OFFICER_EMAIL before public launch",
        "rights": ["access", "correction", "erasure", "grievance", "nomination"],
        "purposes": [{"purpose": k, "required": v["required"], "text": v.get(lang, v["en"])}
                     for k, v in PURPOSES.items()],
    }


class ConsentBody(BaseModel):
    purposes: list[str]


@router.get("/consents")
async def list_consents(cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    return {"active": sorted(await active_purposes(db, cu.account_id)), "notice_version": NOTICE_VERSION}


@router.post("/consents")
async def grant(body: ConsentBody, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    unknown = set(body.purposes) - PURPOSES.keys()
    if unknown:
        raise HTTPException(400, f"Unknown purposes: {sorted(unknown)}")
    active = await active_purposes(db, cu.account_id)
    for p in body.purposes:
        if p not in active:
            db.add(Consent(user_id=cu.account_id, purpose=p, notice_version=NOTICE_VERSION))
            db.add(ConsentEvent(user_id=cu.account_id, purpose=p, event="granted", notice_version=NOTICE_VERSION))
    await db.commit()
    return {"active": sorted(await active_purposes(db, cu.account_id))}


@router.delete("/consents/{purpose}")
async def withdraw(purpose: str, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    rows = await db.scalars(select(Consent).where(Consent.user_id == cu.account_id, Consent.purpose == purpose,
                                                  Consent.withdrawn_at.is_(None)))
    now = utcnow()
    for c in rows:
        c.withdrawn_at = now
        db.add(ConsentEvent(user_id=cu.account_id, purpose=purpose, event="withdrawn", notice_version=c.notice_version))
    await db.commit()
    return {"active": sorted(await active_purposes(db, cu.account_id))}
