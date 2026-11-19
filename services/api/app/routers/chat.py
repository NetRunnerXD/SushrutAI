from __future__ import annotations

import asyncio
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal, get_db
from app.llm import orchestrator
from app.llm.providers import ModelBusy, ProviderUnavailable
from app.models import ChatSession, Document, Message
from app.llm.context_refs import resolve as resolve_refs
from app.ocr.images import classify_attachment, classify_modality, doc_image_b64
from app.security.auth import CurrentUser, current_user
from app.security.settings import cloud_allowed, get_settings_row
from app.security.consent import active_purposes

router = APIRouter(prefix="/chat", tags=["chat"])

# Attachments that have their own reader: chat points to it instead of guessing from a photo.
_IMAGING = ("chest_xray", "other_xray", "ct", "mri", "ultrasound", "eye_fundus", "ecg")
UNSURE_DOC = {
    "en": "This looks like a medical document, but I can't tell for sure whether it is a prescription or a lab report. "
          "Which one is it?",
    "hi": "यह एक मेडिकल दस्तावेज़ लगता है, पर मैं पक्का नहीं कह सकता कि यह पर्चा है या लैब रिपोर्ट। यह कौन-सा है?",
}
ATTACHMENT_ROUTES = {
    "prescription": ("readThisRx", "/medicines/rx?doc={id}", {
        "en": "This looks like a prescription. I can read it, check it for safety and add the medicines to your list "
              "— you check every line before anything is saved.",
        "hi": "यह एक पर्चा लग रहा है। मैं इसे पढ़कर, सुरक्षा जाँचकर दवाएँ आपकी सूची में जोड़ सकता हूँ — सहेजने से "
              "पहले आप हर पंक्ति जाँचेंगे।"}),
    "lab_report": ("readThisReport", "/records?tab=labs&doc={id}", {
        "en": "This looks like a lab report. I can read the values, mark the ones outside the normal range and save "
              "them to your records.",
        "hi": "यह एक लैब रिपोर्ट लग रही है। मैं इसके मान पढ़कर, सामान्य सीमा से बाहर वालों को चिह्नित करके आपके "
              "रिकॉर्ड में सहेज सकता हूँ।"}),
    "scan": ("describeScan", "/records?tab=images&doc={id}", {
        "en": "This looks like a medical scan or X-ray. I can describe what is visible — a radiologist must still "
              "review it.",
        "hi": "यह एक मेडिकल स्कैन या एक्स-रे लग रहा है। मैं बता सकता हूँ कि इसमें क्या दिख रहा है — फिर भी "
              "रेडियोलॉजिस्ट से जाँच ज़रूरी है।"}),
}


def attachment_reply(kind: str, doc_id: str, lang: str) -> dict | None:
    """Structured chat answer for an attachment that has its own reader; None = answer it with the vision model.
    When unsure between prescription and lab report, offer both readers rather than guess."""
    if kind == "unsure_document":
        return {"kind": "attachment", "modality": kind, "reply": UNSURE_DOC.get(lang, UNSURE_DOC["en"]),
                "actions": [{"key": k, "href": h.format(id=doc_id)} for k, h, _ in
                            (ATTACHMENT_ROUTES["prescription"], ATTACHMENT_ROUTES["lab_report"])]}
    if kind not in ATTACHMENT_ROUTES:
        return None
    key, href, msgs = ATTACHMENT_ROUTES[kind]
    return {"kind": "attachment", "modality": kind, "reply": msgs.get(lang, msgs["en"]),
            "actions": [{"key": key, "href": href.format(id=doc_id)}]}


async def detect_attachment(b64: str) -> str:
    """prescription | lab_report | unsure_document | scan | photo. A scan must be confirmed by MedGemma too
    (trained on X-rays): a document is never sent to the X-ray describer on one model's word."""
    try:
        kind = (await classify_attachment(b64))["kind"]
        if kind == "scan" and await classify_modality(b64) not in _IMAGING:
            kind = "photo"
        return kind
    except ProviderUnavailable:
        return "photo"

Lang = Literal["en", "hi", "bn", "ta", "te", "mr", "gu", "kn", "ml", "or"]


class SessionPatch(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    pinned: bool | None = None


class NewSession(BaseModel):
    lang: Lang = "en"
    channel: Literal["text", "voice"] = "text"


class HistoryItem(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class MessageIn(BaseModel):
    text: str = Field(default="", max_length=4000)  # may be empty when a photo is attached
    lang: Lang = "en"
    history: list[HistoryItem] = []  # used when history_storage consent is not given
    image_document_ids: list[str] = Field(default_factory=list, max_length=3)
    context_refs: list[str] = Field(default_factory=list, max_length=3)  # "Ask about this": e.g. ["lab:<id>"]
    model_pref: Literal["auto", "balanced", "fast", "instant", "cloud"] | None = None  # the chat's model switch


@router.post("/sessions", status_code=201)
async def new_session(body: NewSession, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    s = ChatSession(user_id=cu.id, lang=body.lang, channel=body.channel)
    db.add(s)
    await db.commit()
    return {"id": s.id, "lang": s.lang}


@router.get("/suggestions")
async def suggestions(lang: str = "en", session_id: str | None = None, mode: Literal["auto", "rules", "model"] = "auto",
                      cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Tappable 'ask next' chips personalised from this person's data and the last answer (see app/llm/suggest.py)."""
    from app.llm import suggest

    return await suggest.for_user(db, cu, lang, session_id, mode)


@router.get("/sessions")
async def list_sessions(cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    rows = await db.scalars(select(ChatSession).where(ChatSession.user_id == cu.id)
                            .order_by(ChatSession.started_at.desc()))
    out = []
    for s in rows:
        msgs = list(await db.scalars(select(Message).where(Message.session_id == s.id).order_by(Message.created_at)))
        first = next((m for m in msgs if m.role == "user"), None)
        if first is None:  # opened /ask but never asked anything: nothing to resume
            continue
        title = cu.cipher.dec(s.title_enc) if s.title_enc else cu.cipher.dec(first.content_enc)[:60]
        rank = {"self_care": 1, "see_doctor_week": 2, "see_doctor_24h": 3, "emergency": 4}
        urg = max((pc.get("urgency") for m in msgs if m.role == "assistant" and m.structured
                   for pc in (m.structured.get("possible_conditions") or []) if pc.get("urgency") in rank),
                  key=lambda u: rank[u], default=None)
        out.append({"id": s.id, "lang": s.lang, "started_at": s.started_at, "title": title,
                    "pinned": bool(s.pinned), "updated_at": msgs[-1].created_at, "count": len(msgs), "urgency": urg})
    out.sort(key=lambda x: (not x["pinned"], -x["updated_at"].timestamp()))
    return out


async def _own_session(db: AsyncSession, cu: CurrentUser, sid: str) -> ChatSession:
    s = await db.get(ChatSession, sid)
    if s is None or s.user_id != cu.id:
        raise HTTPException(404, "Not found")
    return s


@router.patch("/sessions/{sid}")
async def update_session(sid: str, body: SessionPatch, cu: CurrentUser = Depends(current_user),
                         db: AsyncSession = Depends(get_db)):
    """Rename (empty title = back to the first question) or pin a chat."""
    s = await _own_session(db, cu, sid)
    if body.title is not None:
        s.title_enc = cu.cipher.enc(body.title.strip()) if body.title.strip() else None
    if body.pinned is not None:
        s.pinned = body.pinned
    await db.commit()
    return {"id": s.id, "pinned": bool(s.pinned)}


@router.delete("/sessions/{sid}", status_code=204)
async def delete_session(sid: str, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    s = await _own_session(db, cu, sid)
    await db.execute(delete(Message).where(Message.session_id == sid))
    await db.delete(s)
    await db.commit()


@router.get("/sessions/{sid}")
async def get_session(sid: str, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    s = await db.get(ChatSession, sid)
    if s is None or s.user_id != cu.id:
        raise HTTPException(404, "Not found")
    msgs = await db.scalars(select(Message).where(Message.session_id == sid).order_by(Message.created_at))
    return {"id": s.id, "lang": s.lang, "messages": [
        {"id": m.id, "role": m.role, "content": cu.cipher.dec(m.content_enc), "structured": m.structured,
         "citations": m.citations, "safety": m.safety, "created_at": m.created_at} for m in msgs]}


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@router.post("/sessions/{sid}/messages")
async def post_message(sid: str, body: MessageIn, cu: CurrentUser = Depends(current_user),
                       db: AsyncSession = Depends(get_db)):
    s = await db.get(ChatSession, sid)
    if s is None or s.user_id != cu.id:
        raise HTTPException(404, "Session not found")
    consents = await active_purposes(db, cu.account_id)
    if "core_service" not in consents:
        raise HTTPException(403, "Please accept the privacy notice (core_service consent) first")
    store = "history_storage" in consents
    allow_cloud = await cloud_allowed(db, cu.account_id, "cloud_llm_deidentified")
    model_pref = body.model_pref or (await get_settings_row(db, cu.account_id)).model_pref or "auto"

    if not body.text.strip() and not body.image_document_ids:
        raise HTTPException(422, "Type a question or attach a photo")
    attachments: list[tuple[str, str]] = []  # (document id, JPEG as base64): PDFs rendered, photos resized
    for did in body.image_document_ids:
        b64, _ = await doc_image_b64(cu, db, did, max_side=1024)
        attachments.append((did, b64))
    images = [b for _, b in attachments]
    context, hint_kind = await resolve_refs(db, cu, body.context_refs) if body.context_refs else ("", None)
    text = body.text.strip() or {"hi": "इस तस्वीर में क्या दिख रहा है?"}.get(body.lang, "What does this show?")

    if store:
        prior = await db.scalars(select(Message).where(Message.session_id == sid).order_by(Message.created_at))
        history = [{"role": m.role, "content": cu.cipher.dec(m.content_enc),
                    **({"structured": m.structured} if m.role == "assistant" and m.structured else {})} for m in prior]
    else:
        history = [h.model_dump() for h in body.history]

    async def gen():
        yield _sse("status", {"stage": "reading_attachment" if attachments else "thinking"})
        async with SessionLocal() as wdb:  # request-scoped session is closed once streaming starts
            if attachments:
                structured = attachment_reply(await detect_attachment(attachments[0][1]), attachments[0][0], body.lang)
                if structured is not None:
                    reply = structured["reply"]
                    payload = {"reply": reply, "structured": structured, "citations": [], "disclaimer": "",
                               "stored": store}
                    if store:
                        wdb.add(Message(session_id=sid, role="user", content_enc=cu.cipher.enc(text), lang=body.lang))
                        m = Message(session_id=sid, role="assistant", content_enc=cu.cipher.enc(reply), lang=body.lang,
                                    structured=structured, citations=[], safety={})
                        wdb.add(m)
                        await wdb.commit()
                        payload["message_id"] = m.id
                    yield _sse("answer", payload)
                    yield _sse("done", {})
                    return
                yield _sse("status", {"stage": "thinking"})
            drafts: asyncio.Queue[str] = asyncio.Queue()
            task = asyncio.create_task(orchestrator.answer(wdb, cu, text, lang=body.lang, history=history,
                                                           images=images or None, allow_cloud=allow_cloud,
                                                           context=context, hint_kind=hint_kind,
                                                           on_delta=drafts.put_nowait, model_pref=model_pref))
            try:
                # "delta" carries the whole reply written so far (a draft; "answer" replaces it). Only the newest
                # draft is sent, at most ~8 times a second; a comment line keeps quiet connections open.
                while not task.done():
                    try:
                        latest = await asyncio.wait_for(drafts.get(), timeout=2)
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    await asyncio.sleep(0.12)
                    while not drafts.empty():
                        latest = drafts.get_nowait()
                    if not task.done():
                        yield _sse("delta", {"text": latest})
            finally:
                if not task.done():  # client pressed Stop or went away: stop the model too
                    task.cancel()
            try:
                r = task.result()
            except ProviderUnavailable as e:
                yield _sse("error", {"message": str(e), "hint": e.hint, "busy": isinstance(e, ModelBusy)})
                return
            if r.red_flag.get("triggered"):
                yield _sse("red_flag", r.red_flag)
            payload = {"reply": r.reply, "structured": r.structured, "citations": r.citations,
                       "disclaimer": r.disclaimer, "provider": r.provider, "model": r.model, "tier": r.tier, "speed": r.speed, "auto": model_pref == "auto",
                       "latency_ms": r.latency_ms, "stored": store}
            if store:
                wdb.add(Message(session_id=sid, role="user", content_enc=cu.cipher.enc(text), lang=body.lang))
                m = Message(session_id=sid, role="assistant", content_enc=cu.cipher.enc(r.reply), lang=body.lang,
                            structured=r.structured, citations=r.citations, safety={**r.red_flag, "warnings": r.warnings},
                            provider=r.provider, model=r.model, privacy_tier=r.tier, latency_ms=r.latency_ms)
                wdb.add(m)
                await wdb.commit()
                payload["message_id"] = m.id
            yield _sse("answer", payload)
            yield _sse("done", {})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
