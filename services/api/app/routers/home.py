"""Home screen data: the daily check-in and a seasonal health tip."""
from __future__ import annotations

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import local_today
from app.db import get_db
from app.models import CheckIn, PatientProfile
from app.models import _now as utcnow
from app.security.auth import CurrentUser, current_user

router = APIRouter(prefix="/me", tags=["home"])


class CheckInIn(BaseModel):
    mood: Literal["good", "okay", "unwell"]
    note: str | None = Field(None, max_length=300)


@router.post("/checkins")
async def check_in(body: CheckInIn, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    today = local_today()
    row = (await db.scalars(select(CheckIn).where(CheckIn.user_id == cu.id, CheckIn.day == today))).first()
    if row is None:
        row = CheckIn(user_id=cu.id, day=today, mood=body.mood)
        db.add(row)
    row.mood, row.at = body.mood, utcnow()
    row.note_enc = cu.cipher.enc(body.note) if body.note else None
    await db.commit()
    return {"day": today, "mood": row.mood}


@router.get("/checkins")
async def check_ins(days: int = 14, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    since = local_today() - timedelta(days=max(1, min(days, 90)) - 1)
    rows = await db.scalars(select(CheckIn).where(CheckIn.user_id == cu.id, CheckIn.day >= since).order_by(CheckIn.day))
    out = [{"day": r.day, "mood": r.mood, "note": cu.cipher.dec(r.note_enc) if r.note_enc else None} for r in rows]
    today = local_today()
    return {"today": next((r["mood"] for r in out if r["day"] == today), None), "items": out}


# Seasonal prevention advice. Plain, widely published public-health guidance; each tip names the source agency.
TIPS = {
    "monsoon": {
        "en": "Monsoon: empty and scrub water containers, coolers and pots once a week, and sleep under a net. "
              "Fever with body ache or chills now needs a test for dengue and malaria — see a doctor early.",
        "hi": "मानसून: हर हफ्ते पानी के बर्तन, कूलर और गमले खाली करके साफ़ करें, और मच्छरदानी में सोएँ। "
              "इस मौसम में बुखार के साथ बदन दर्द या ठंड लगे तो डेंगू और मलेरिया की जाँच ज़रूरी है — जल्दी डॉक्टर को दिखाएँ।",
        "source": "National Center for Vector Borne Diseases Control (NCVBDC), MoHFW", "url": "https://ncvbdc.mohfw.gov.in/",
    },
    "summer": {
        "en": "Heat: drink water often even if not thirsty, avoid the sun between 12 and 3 pm, and use ORS if you sweat a "
              "lot. Confusion, very hot dry skin or fainting in the heat is an emergency — call 108.",
        "hi": "गर्मी: प्यास न लगे तब भी बार-बार पानी पिएँ, दोपहर 12 से 3 बजे धूप से बचें, और ज़्यादा पसीना आए तो ORS लें। "
              "गर्मी में बेहोशी, भ्रम या बहुत गर्म सूखी त्वचा आपात स्थिति है — 108 पर कॉल करें।",
        "source": "National Disaster Management Authority (NDMA) — heat wave guidance", "url": "https://ndma.gov.in/",
    },
    "winter": {
        "en": "Winter: on high-pollution days limit outdoor exercise, especially with asthma, COPD or heart disease. "
              "Keep rooms ventilated when using a coal or wood heater — the fumes can be deadly.",
        "hi": "सर्दी: ज़्यादा प्रदूषण वाले दिनों में बाहर व्यायाम कम करें, खासकर अस्थमा, COPD या हृदय रोग में। "
              "कोयले या लकड़ी की अंगीठी जलाएँ तो कमरे में हवा आने दें — इसका धुआँ जानलेवा हो सकता है।",
        "source": "Ministry of Health & Family Welfare (MoHFW) — climate & health advisories", "url": "https://mohfw.gov.in/",
    },
}


def season_of(month: int) -> str:
    return "monsoon" if month in (6, 7, 8, 9, 10, 11) else "summer" if month in (3, 4, 5) else "winter"


@router.get("/tip")
async def tip(lang: str = "en", cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    month = utcnow().month
    p = await db.get(PatientProfile, cu.id)
    # Kerala and Tamil Nadu get the north-east monsoon rains in Oct–Dec: keep the mosquito advice through December
    season = "monsoon" if (p and p.state_code in ("KL", "TN") and month == 12) else season_of(month)
    t = TIPS[season]
    return {"season": season, "text": t.get(lang, t["en"]), "source": t["source"], "url": t["url"]}
