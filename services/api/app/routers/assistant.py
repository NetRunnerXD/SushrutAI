"""POST /assistant/parse — what should the app *do* with what the person just said?

Returns an action for the web app to carry out. Read-only actions (dose status, latest report, navigate,
switch profile) come back with the sentence to speak. Actions that save data (log a reading, mark a dose) come
back as a proposal with confirm=true: the app asks "Save BP 130/85?" and only writes after a yes.
Returns {"action": null} when the words are a question for the assistant instead.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.assistant.actions import describe_readings, parse
from app.clock import local_now
from app.db import get_db
from app.models import LabResult, Medication
from app.routers import family as family_router
from app.routers import records as records_router
from app.security.auth import CurrentUser, current_user

router = APIRouter(prefix="/assistant", tags=["assistant"])

SLOT_HI = {"morning": "सुबह", "afternoon": "दोपहर", "night": "रात", "as_needed": "ज़रूरत पर"}
WHEN_EN = {"morning": "this morning", "afternoon": "this afternoon", "night": "tonight"}


class ParseIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    lang: str = "en"


def _now_slot() -> str:
    h = local_now().hour
    return "morning" if h < 12 else "afternoon" if h < 17 else "night"


def _say(lang: str, en: str, hi: str) -> str:
    return hi if lang == "hi" else en


@router.post("/parse")
async def parse_command(body: ParseIn, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    lang = body.lang
    meds = [{"id": m.id, "name": cu.cipher.dec(m.name_enc)}
            for m in await db.scalars(select(Medication).where(Medication.user_id == cu.id, Medication.status == "active"))]
    family = await family_router.list_members(cu=cu, db=db)
    a = parse(body.text, meds=meds, family=family)
    if a is None:
        return {"action": None}
    who = f" for {family_name}" if (family_name := _member_name(cu, family)) else ""
    who_hi = f" ({family_name})" if family_name else ""
    out: dict = {"action": a.action, "params": a.params, "confirm": a.confirm}

    if a.action == "log_vital":
        desc = describe_readings(a.params["readings"], lang)
        out["say"] = _say(lang, f"Save {desc}{who}?", f"क्या {desc}{who_hi} सहेजें?")

    elif a.action == "log_dose":
        today = await records_router.doses(day=None, cu=cu, db=db)
        wanted = a.params.get("slot") or _now_slot()
        items = []
        for m in a.params["medicines"]:
            slots = [i for i in today["items"] if i["medication_id"] == m["id"]]
            if not slots:
                continue
            pick = next((i for i in slots if i["slot"] == wanted), None) \
                or next((i for i in slots if i["status"] != "taken"), slots[0])
            items.append({"medication_id": m["id"], "name": m["name"], "slot": pick["slot"]})
        if not items:
            return {"action": "say", "say": _say(lang, "I couldn't find that medicine in today's schedule.",
                                                 "आज की सूची में यह दवा नहीं मिली।")}
        out["params"] = {"items": items}
        names = ", ".join(f"{i['name']} ({i['slot'] if lang != 'hi' else SLOT_HI.get(i['slot'], i['slot'])})" for i in items)
        out["say"] = _say(lang, f"Mark {names} as taken{who}?", f"क्या {names}{who_hi} ली गई मान लें?")

    elif a.action == "dose_status":
        today = await records_router.doses(day=None, cu=cu, db=db)
        slot = a.params.get("slot") or _now_slot()
        due = [i for i in today["items"] if i["slot"] == slot]
        if not today["items"]:
            out["say"] = _say(lang, "No medicines are scheduled for today.", "आज के लिए कोई दवा निर्धारित नहीं है।")
        elif not due:
            out["say"] = _say(lang, f"Nothing is scheduled for {WHEN_EN.get(slot, slot)}.", f"{SLOT_HI.get(slot, slot)} के लिए कोई दवा नहीं है।")
        else:
            taken = [i["name"] for i in due if i["status"] == "taken"]
            left = [i["name"] for i in due if i["status"] != "taken"]
            when = WHEN_EN.get(slot, slot)
            en = (f"{when.capitalize()}: taken {', '.join(taken)}. " if taken else f"Nothing marked as taken {when} yet. ") + \
                 (f"Not yet: {', '.join(left)}." if left else "All done.")
            hi = (f"{SLOT_HI[slot]} की ली गई दवाएँ: {', '.join(taken)}। " if taken else f"{SLOT_HI[slot]} की कोई दवा अभी ली गई नहीं दिखी। ") + \
                 (f"अभी बाकी: {', '.join(left)}।" if left else "सब ले ली गई हैं।")
            out["say"] = _say(lang, en, hi)
            out["params"] = {"slot": slot, "left": left, "taken": taken}

    elif a.action == "read_latest":
        labs = list(await db.scalars(select(LabResult).where(LabResult.user_id == cu.id).order_by(LabResult.observed_at.desc())))
        if not labs:
            out["say"] = _say(lang, "You have no lab reports saved yet.", "अभी कोई लैब रिपोर्ट सहेजी नहीं है।")
        else:
            key = labs[0].document_id
            latest = [l for l in labs if l.document_id == key] if key else labs[:1]
            odd = [l for l in latest if l.flag in ("H", "L")]
            when = labs[0].observed_at.strftime("%d %b")
            odd_txt = "; ".join(f"{l.test_name} {l.value_text or l.value} {l.unit or ''} {'high' if l.flag == 'H' else 'low'}".strip() for l in odd)
            en = f"Your report from {when} has {len(latest)} results. " + \
                 (f"Outside the normal range: {odd_txt}." if odd else "All the values with a range are within it.")
            hi = f"{when} की रिपोर्ट में {len(latest)} नतीजे हैं। " + \
                 (f"सामान्य सीमा से बाहर: {odd_txt}।" if odd else "जिनकी सीमा दी है, वे सब सीमा के अंदर हैं।")
            out["say"] = _say(lang, en, hi)
            out["href"] = "/records?tab=labs"

    elif a.action == "navigate":
        out["href"] = a.params["href"]
        out["say"] = _say(lang, "Opening it.", "खोल रहे हैं।")

    elif a.action == "switch_profile":
        name = a.params.get("name")
        out["say"] = _say(lang, f"Switching to {name}." if name else "Switching to your own record.",
                          f"{name} का रिकॉर्ड खोल रहे हैं।" if name else "आपका अपना रिकॉर्ड खोल रहे हैं।")
    return out


def _member_name(cu: CurrentUser, family: list[dict]) -> str | None:
    if not cu.is_member:
        return None
    return next((f["name"] for f in family if f["id"] == cu.id), None)
