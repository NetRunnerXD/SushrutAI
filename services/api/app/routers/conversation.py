"""Stateful conversation orchestrator, turn merging, pause/resume, and barge-in API endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.clinical.persistence import get_or_create_encounter
from app.conversation.state import ConversationState, conversation_orchestrator
from app.db import get_db
from app.memory.context import PatientContextBuilder
from app.models import ChatSession
from app.security.auth import CurrentUser, current_user

router = APIRouter(prefix="/conversation", tags=["conversation"])


async def _encounter_for_chat(db: AsyncSession, cu: CurrentUser, sid: str):
    s = await db.get(ChatSession, sid)
    if s is None or s.user_id != cu.id:
        raise HTTPException(404, "Chat session not found")
    enc = await get_or_create_encounter(db, cu, chat_session_id=sid, channel=s.channel or "voice", language=s.lang or "en")
    await db.commit()
    return enc


@router.get("/sessions/{sid}/state")
async def get_state(sid: str, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    enc = await _encounter_for_chat(db, cu, sid)
    snap = conversation_orchestrator.snapshot(enc, cu)
    mem = await PatientContextBuilder().build(db, cu, query_text=snap["open_fragment"], encounter_id=enc.id)
    snap["pending_questions"] = mem.pending_questions
    snap["active_symptoms"] = mem.short_term.get("active_symptoms", [])
    return snap


class TransitionIn(BaseModel):
    state: ConversationState
    reason: str = Field(default="", max_length=160)
    strict: bool = False


@router.post("/sessions/{sid}/state")
async def set_state(sid: str, body: TransitionIn, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    enc = await _encounter_for_chat(db, cu, sid)
    try:
        return await conversation_orchestrator.transition(db, cu, enc.id, body.state, reason=body.reason, strict=body.strict)
    except ValueError as e:
        raise HTTPException(409, str(e))


class FragmentIn(BaseModel):
    text: str = Field(max_length=2000)


@router.post("/sessions/{sid}/fragment")
async def merge_fragment(sid: str, body: FragmentIn, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    enc = await _encounter_for_chat(db, cu, sid)
    return await conversation_orchestrator.append_fragment(db, cu, enc.id, body.text)


@router.post("/sessions/{sid}/finalize")
async def finalize_turn(sid: str, body: FragmentIn | None = None, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    enc = await _encounter_for_chat(db, cu, sid)
    return await conversation_orchestrator.finalize_turn(db, cu, enc.id, (body.text if body else ""))


class BargeInReq(BaseModel):
    spoken_prefix: str | None = Field(default=None, max_length=1000)
    interruption_text: str = Field(default="", max_length=2000)
    cancelled_generation_id: str | None = Field(default=None, max_length=80)


@router.post("/sessions/{sid}/barge-in")
async def barge_in(sid: str, body: BargeInReq, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    enc = await _encounter_for_chat(db, cu, sid)
    return await conversation_orchestrator.handle_barge_in(
        db,
        cu,
        enc.id,
        spoken_prefix=body.spoken_prefix,
        interruption_text=body.interruption_text,
        cancelled_generation_id=body.cancelled_generation_id,
    )


@router.post("/sessions/{sid}/pause")
async def pause_session(sid: str, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    enc = await _encounter_for_chat(db, cu, sid)
    res = await conversation_orchestrator.transition(db, cu, enc.id, ConversationState.SESSION_PAUSED, reason="User or inactivity pause")
    return {**res, **conversation_orchestrator.snapshot(enc, cu)}


@router.post("/sessions/{sid}/resume")
async def resume_session(sid: str, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    enc = await _encounter_for_chat(db, cu, sid)
    res = await conversation_orchestrator.transition(db, cu, enc.id, ConversationState.LISTENING, reason="Session resumed")
    mem = await PatientContextBuilder().build(db, cu, encounter_id=enc.id)
    snap = conversation_orchestrator.snapshot(enc, cu)
    snap["pending_questions"] = mem.pending_questions
    snap["active_symptoms"] = mem.short_term.get("active_symptoms", [])
    return {**res, **snap}
