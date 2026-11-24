from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.llm.orchestrator import LANG_NAMES, profile_summary
from app.llm.providers import ProviderUnavailable, clinical_chat
from app.models import PatientProfile, TriageSession
from app.safety import red_flags
from app.safety.disclaimer import disclaimer
from app.security.auth import CurrentUser, current_user
from app.security.settings import cloud_allowed
from app.security.consent import active_purposes
from app.triage import engine
from app.triage.urgency import recommend, recommended_specialist
from app.triage.store import is_encrypted, load_state, save_state
from app.langs import LangCode

router = APIRouter(prefix="/triage", tags=["triage"])



def _season_hint(today: date) -> str:
    m = today.month
    if m in (6, 7, 8, 9, 10):
        return "It is monsoon/post-monsoon season in India (higher dengue, malaria, typhoid, leptospirosis risk)."
    if m in (3, 4, 5):
        return "It is summer in India (heat illness, diarrhoeal disease more common)."
    return "It is winter in India (respiratory infections, influenza more common)."


class StartIn(BaseModel):
    complaint: str = Field(min_length=3, max_length=1000)
    lang: LangCode = "en"


class AnswerIn(BaseModel):
    finding_id: str
    answer: Literal["yes", "no", "unsure"]


def _view(ts: TriageSession, st: dict) -> dict:
    if st.get("emergency"):
        return {"id": ts.id, "status": ts.status, "red_flag": st["emergency"], "disclaimer": disclaimer(st["lang"])}
    done, reason = engine.should_stop(st)
    flags = engine.red_flag_answered(st)
    q = None if (done or flags) else engine.next_question(st)
    ranked = engine.summary(st)
    out = {
        "id": ts.id, "status": "complete" if (done or flags) else "asking", "asked": st["asked"],
        "question": None if q is None else {"finding_id": q["id"], "text": q["question"],
                                            "options": ["yes", "no", "unsure"]},
        "possible_conditions": ranked[:5], "stop_reason": reason or ("red_flag" if flags else None),
        "disclaimer": disclaimer(st["lang"]),
        "also_consider": st.get("missing_must_consider") or [],
    }
    if out["also_consider"]:
        out["also_consider_note"] = ("Given your symptoms, season and region, doctors in India would also check for: "
                                     + ", ".join(out["also_consider"]) + ". Ask your doctor about testing.")
    if flags:
        out["red_flag"] = {"triggered": True, "kind": "urgent", "answered_yes": flags,
                           "messages": ["You reported a danger sign. Please see a doctor today, or call 108 / 112 "
                                        "if it is severe or getting worse."], "call_numbers": ["108", "112"]}
    if out["status"] == "complete":
        rec = recommend(ranked, red_flag_yes=bool(flags))
        out["recommendation"] = {"urgency": rec, "specialist": recommended_specialist(ranked, rec)}
    return out


@router.post("/sessions", status_code=201)
async def start(body: StartIn, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    if "core_service" not in await active_purposes(db, cu.account_id):
        raise HTTPException(403, "Please accept the privacy notice first")
    profile, age = await profile_summary(db, cu)
    rf = red_flags.check(body.complaint, lang=body.lang, age_years=age)
    if rf.triggered and rf.kind in ("emergency", "mental_health"):
        st = {"lang": body.lang, "emergency": rf.as_dict()}
        ts = TriageSession(user_id=cu.id, status="closed")
        save_state(ts, cu.cipher, st)
        db.add(ts)
        await db.commit()
        return _view(ts, st)
    p = await db.get(PatientProfile, cu.id)
    required = engine.must_consider(body.complaint, p.state_code if p else None, date.today().month)
    msgs = [{"role": "system", "content": "You are a clinical reasoning assistant building a symptom-checker "
                                          "question plan. Be medically accurate and conservative."},
            {"role": "user", "content": engine.build_prompt(body.complaint, profile,
                                                            LANG_NAMES.get(body.lang, "English"),
                                                            _season_hint(date.today()), required)}]
    try:
        cloud_ok = await cloud_allowed(db, cu.account_id, "cloud_llm_deidentified")
        llm = (await clinical_chat(msgs, schema=engine.TRIAGE_SCHEMA, allow_cloud=cloud_ok, prefer_cloud=cloud_ok,
                                   num_predict=1400)).json()
    except ProviderUnavailable as e:
        raise HTTPException(503, e.hint) from e
    except ValueError as e:
        raise HTTPException(502, "The model returned an incomplete question plan — please try again.") from e
    try:
        state = engine.init_state(llm, sex=p.sex if p else None, age=age,
                                  pregnant=bool(p and p.pregnancy_status in ("pregnant", "possibly_pregnant")))
    except ValueError as e:
        raise HTTPException(502, f"Model returned an unusable plan: {e}") from e
    state["lang"], state["complaint"] = body.lang, body.complaint
    state["must_consider"] = required
    state["missing_must_consider"] = engine.missing_required(llm, required)  # surfaced to the user if non-empty
    ts = TriageSession(user_id=cu.id)
    save_state(ts, cu.cipher, state)
    db.add(ts)
    await db.commit()
    return _view(ts, state)


@router.post("/sessions/{tid}/answer")
async def answer(tid: str, body: AnswerIn, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    ts = await db.get(TriageSession, tid)
    if ts is None or ts.user_id != cu.id:
        raise HTTPException(404, "Not found")
    if ts.status != "open":
        raise HTTPException(409, "This triage session is finished")
    st = load_state(ts, cu.cipher)
    try:
        engine.update(st, body.finding_id, body.answer)
    except KeyError as e:
        raise HTTPException(400, "Unknown question") from e
    save_state(ts, cu.cipher, st)
    v = _view(ts, st)
    if v["status"] == "complete":
        ts.status = "closed"
    await db.commit()
    return v


@router.get("/sessions/{tid}")
async def get(tid: str, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    ts = await db.get(TriageSession, tid)
    if ts is None or ts.user_id != cu.id:
        raise HTTPException(404, "Not found")
    st = load_state(ts, cu.cipher)
    if not is_encrypted(ts.state):  # legacy plaintext row: encrypt it now
        save_state(ts, cu.cipher, st)
        await db.commit()
    return _view(ts, st)
