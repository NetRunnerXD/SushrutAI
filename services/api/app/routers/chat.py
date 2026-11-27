from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal, get_db
from app.llm import orchestrator
from app.llm.providers import ModelBusy, ProviderUnavailable, full_cloud_allowed
from app.models import ChatSession, Document, Message
from app.llm.context_refs import resolve as resolve_refs
from app.ocr.images import classify_attachment, classify_modality, doc_image_b64
from app.security.auth import CurrentUser, current_user
from app.medicine.pack import MED_PHOTO_Q
from app.security.settings import cloud_allowed, get_settings_row
from app.security.consent import active_purposes
from app.langs import LangCode

log = logging.getLogger(__name__)
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

Lang = LangCode


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
    model_pref: Literal["auto", "balanced", "fast", "instant", "cloud", "gemini", "gemini_fast"] | None = None  # the chat's model switch
    replace_history: bool = False  # the screen's thread is the one to keep (edit / answer again)
    channel: Literal["text", "voice"] = "text"
    voice_meta: dict | None = None  # raw_transcript, normalized_transcript, asr_confidence, asr_engine, segments, code_switched
    generation_id: str | None = None
    spoken_prefix: str | None = None  # assistant prefix spoken aloud before patient barge-in
    force_english: bool = False  # spoken question: answer in English, then translate back to the heard language
    preferred_response_language: Lang | None = None  # explicit response language preference when set
    context_scope: Literal["chat", "record"] = "chat"  # chat: this thread only. record: read and update the patient record


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


@router.delete("/sessions", status_code=204)
async def delete_all_sessions(cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Delete every chat of this person (all messages included). Other people's chats are never touched."""
    ids = list(await db.scalars(select(ChatSession.id).where(ChatSession.user_id == cu.id)))
    if ids:
        await db.execute(delete(Message).where(Message.session_id.in_(ids)))
        await db.execute(delete(ChatSession).where(ChatSession.id.in_(ids)))
    await db.commit()


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


async def medicine_photo_reply(db: AsyncSession, cu: CurrentUser, b64: str, text: str, lang: str,
                               model_pref: str = "balanced") -> dict | None:
    """Answer for a photo of a medicine pack, or None when it is not one."""
    from app.medicine.pack import SAME_Q, compare_with_prescribed, read_pack
    from app.medicine.resolve import resolve
    from app.medicine.storyline import storyline

    pack = await read_pack(b64, model_pref=model_pref)
    if pack is None:
        return None
    r = await resolve(db, pack["brand"] or pack["composition"], printed=pack["composition"])
    story = await storyline(db, cu, r, given_by_doctor=False)
    cmp = await compare_with_prescribed(db, cu, r)
    reply = ((cmp["say"] + " ") if cmp and SAME_Q.search(text) else "") + story["text"]
    out = {"kind": "medicine_photo", "reply": reply, "storylines": [story], "comparison": cmp,
           "pack": pack, "suggest_check": False}
    if lang != "en":
        from app.llm.orchestrator import translate_answer
        await translate_answer(out, lang)
    return out


MEMORY_SKIP_KINDS = {"greeting", "thanks_bye", "about_app", "off_topic"}


async def _persist_clinical_turn(
    db: AsyncSession,
    cu: CurrentUser,
    *,
    sid: str,
    body: MessageIn,
    user_text: str,
    result: orchestrator.TurnResult,
) -> dict:
    """Validate, refine transcript, extract facts with span highlights, run clinical reasoning,
    select dynamic uncapped interview question, recommend tailored investigations, and update state."""
    import os
    from app.clinical.extract import extract_clinical_turn
    from app.clinical.facts import record_clinical_facts
    from app.clinical.followup import record_followup_answer
    from app.clinical.interview import AdaptiveClinicalInterviewEngine
    from app.clinical.investigations import InvestigationRecommendationEngine
    from app.clinical.persistence import (
        get_or_create_encounter,
        persist_extraction,
        record_recommendation,
        record_safety_alert,
        record_turn,
    )
    from app.clinical.reasoning import ClinicalReasoningEngine
    from app.conversation.state import ConversationState, conversation_orchestrator
    from app.memory.context import PatientContextBuilder
    from app.safety.clinical_safety import evaluate_safety
    from app.speech.refine import persist_transcript, refine_transcript

    vm = body.voice_meta or {}
    raw_text = str(vm.get("raw_transcript") or user_text)
    in_isolated_test = os.environ.get("OLLAMA_BASE_URL", "").endswith(":9")
    refine_res = await refine_transcript(
        raw_text,
        lang_hint=str(vm.get("lang") or body.lang),
        audio_conf=vm.get("asr_confidence"),
        use_llm=not in_isolated_test and body.channel == "voice",
    )
    analysis = refine_res.analysis
    norm_text = str(vm.get("normalized_transcript") or refine_res.corrected_transcript or analysis.normalized_transcript or raw_text)
    det_lang = str(vm.get("lang") or analysis.primary_language or body.lang)
    code_switched = bool(vm.get("code_switched", analysis.code_switched))

    enc = await get_or_create_encounter(
        db,
        cu,
        chat_session_id=sid,
        channel=body.channel,
        language=det_lang,
        chief_complaint=norm_text[:240],
    )
    user_turn = await record_turn(
        db,
        cu,
        encounter_id=enc.id,
        session_id=sid,
        role="user",
        raw_text=raw_text,
        normalized_text=norm_text,
        detected_lang=det_lang,
        code_switched=code_switched,
        asr_confidence=vm.get("asr_confidence") or analysis.confidence,
        asr_engine=vm.get("asr_engine"),
        speech_segments=vm.get("segments"),
    )
    transcript_row = await persist_transcript(
        db,
        cu,
        encounter_id=enc.id,
        turn_id=user_turn.id,
        speaker="patient",
        result=refine_res,
    )

    nlp_input = f"{norm_text}\n{result.english}" if result.english and result.english.strip() != norm_text.strip() else norm_text
    skip_llm = in_isolated_test or (result.kind or "") in MEMORY_SKIP_KINDS or bool(result.red_flag.get("triggered"))
    extraction = await extract_clinical_turn(
        nlp_input,
        patient_id=cu.id,
        encounter_id=enc.id,
        language=det_lang,
        raw_transcript=raw_text,
        source="voice_transcript" if body.channel == "voice" else "chat_text",
        use_llm=not skip_llm and body.model_pref not in ("instant",),
    )
    user_turn.extracted_payload_enc = cu.cipher.enc(json.dumps(extraction.model_dump(), ensure_ascii=False, default=str))

    persist_summary = await persist_extraction(
        db, cu, extraction, encounter_id=enc.id, turn_id=user_turn.id
    )
    await record_clinical_facts(
        db,
        cu,
        encounter_id=enc.id,
        conversation_id=sid,
        turn_id=user_turn.id,
        transcript_id=transcript_row.id,
        transcript_text=raw_text,
        payload=extraction,
        discrepancy_notes=persist_summary.get("discrepancies"),
    )
    completed_fqs = await record_followup_answer(
        db, cu, encounter_id=enc.id, answer_text=norm_text, extraction=extraction
    )

    safety = evaluate_safety(nlp_input, lang=body.lang, extraction=extraction)
    if result.red_flag.get("triggered") or safety.escalate_now:
        rules = list(dict.fromkeys((result.red_flag.get("rules") or []) + safety.rule_ids))
        msgs = list(dict.fromkeys((result.red_flag.get("messages") or []) + safety.messages))
        nums = list(dict.fromkeys((result.red_flag.get("call_numbers") or []) + safety.call_numbers))
        await record_safety_alert(
            db,
            cu,
            encounter_id=enc.id,
            turn_id=user_turn.id,
            severity=result.red_flag.get("kind") or safety.severity,
            rule_ids=rules,
            guidance="\n".join(msgs)[:1500] or result.reply[:500],
            call_numbers=nums,
        )

    mem_ctx = await PatientContextBuilder().build(
        db,
        cu,
        query_text=nlp_input,
        current_symptoms=[s.name for s in extraction.symptoms],
        encounter_id=enc.id,
    )

    assessment_info = await ClinicalReasoningEngine().evaluate_and_persist(
        db,
        cu,
        encounter_id=enc.id,
        user_text=norm_text,
        extraction=extraction,
        mem=mem_ctx,
        safety=safety,
    )

    investigations = []
    if result.structured is not None and (result.kind or "") not in MEMORY_SKIP_KINDS:
        investigations = await InvestigationRecommendationEngine().recommend(
            db, cu, encounter_id=enc.id, mem=mem_ctx, safety=safety, user_text=nlp_input
        )

    interview_res = {"outcome": "SUFFICIENT_INFORMATION", "question": None, "candidates_remaining": 0}
    if result.structured is not None and (result.kind or "") not in (*MEMORY_SKIP_KINDS, "clarify"):
        interview_res = await AdaptiveClinicalInterviewEngine().evaluate_and_next_question(
            db,
            cu,
            encounter_id=enc.id,
            user_text=norm_text,
            extraction=extraction,
            mem=mem_ctx,
            safety=safety,
            lang=body.lang,
        )
    followup_q = interview_res.get("question")

    if isinstance(result.structured, dict) and (result.kind or "") not in MEMORY_SKIP_KINDS:
        pcs = result.structured.get("possible_conditions") or []
        urg = (pcs[0].get("urgency") if pcs else None) or ("emergency" if safety.escalate_now else "see_doctor_week")
        spec = result.structured.get("specialist")
        await record_recommendation(
            db,
            cu,
            encounter_id=enc.id,
            urgency=urg,
            specialist=spec,
            summary_text=result.reply[:2000],
            self_care=result.structured.get("self_care") or result.structured.get("tips"),
            watch_for=result.structured.get("watch_for"),
        )

    await record_turn(
        db,
        cu,
        encounter_id=enc.id,
        session_id=sid,
        role="assistant",
        raw_text=result.reply,
        normalized_text=result.reply,
        detected_lang=body.lang,
        extracted_payload={
            "kind": result.kind,
            "followup_question": followup_q,
            "interview_outcome": interview_res.get("outcome"),
            "spoken_prefix": body.spoken_prefix,
        },
    )
    target_state = (
        ConversationState.CLARIFICATION
        if refine_res.needs_clarification
        else ConversationState.SPEAKING if body.channel == "voice" else ConversationState.IDLE
    )
    await conversation_orchestrator.transition(db, cu, enc.id, target_state, reason="Turn processed")

    return {
        "encounter_id": enc.id,
        "transcript_id": transcript_row.id,
        "generation_id": conversation_orchestrator.runtime_for(enc.id, cu.id).generation_id,
        "state": target_state.value,
        "language": det_lang,
        "raw_transcript": raw_text,
        "corrected_transcript": norm_text,
        "normalized_transcript": norm_text,
        "code_switched": code_switched,
        "safety_preserved": refine_res.safety_preserved,
        "needs_clarification": refine_res.needs_clarification,
        "clarification_prompt": refine_res.clarification_prompt,
        "returning_patient": mem_ctx.returning_patient,
        "prior_encounter_summary": mem_ctx.prior_encounter_summary,
        "completed_followups": completed_fqs,
        "persisted": persist_summary,
        "extraction": extraction.model_dump(),
        "safety": safety.as_dict(),
        "assessment": assessment_info,
        "investigations": investigations,
        "interview_outcome": interview_res.get("outcome"),
        "followup_question": followup_q,
    }


async def remember_turn(db: AsyncSession, cu: CurrentUser, text: str, english: str, sid: str) -> list[dict]:
    from app.memory.extract import quick_facts
    from app.memory.store import save_facts

    facts = quick_facts(text)
    if english:  # a translated question: read the English too, keep the first of each name
        names = {f.name for f in facts}
        facts += [f for f in quick_facts(english) if f.name not in names]
    try:
        return await save_facts(db, cu, facts, source="chat", source_ref=sid)
    except Exception:  # noqa: BLE001 — memory must never break an answer
        log.exception("saving facts failed")
        return []


async def remember_turn_later(cu: CurrentUser, text_en: str, sid: str) -> None:
    """The model pass: symptoms present / absent, severity, triggers, medicine responses, context anchors."""
    from app.memory.extract import model_facts
    from app.memory.store import save_facts

    try:
        facts = await model_facts(text_en)
        if facts:
            async with SessionLocal() as db:
                await save_facts(db, cu, facts, source="chat", source_ref=sid, write_vitals=False)
    except Exception:  # noqa: BLE001
        log.exception("background memory extraction failed")


def _history_prefix(client: list[dict], stored: list[dict]) -> bool:
    """True when the screen's thread is the leading part of the saved one (including both empty)."""
    if len(client) > len(stored):
        return False
    for item, row in zip(client, stored):
        if item.get("role") != row.get("role"):
            return False
        if (item.get("content") or "").strip() != (row.get("content") or "").strip():
            return False
    return True


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
    allow_cloud = await cloud_allowed(db, cu.account_id, "cloud_llm_deidentified")  # Groq: scrubbed text, never photos
    allow_full = await cloud_allowed(db, cu.account_id, "cloud_llm_full")  # Gemini, only when the person chose it
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
    from app.speech.normalize import normalize_banglish_hinglish, resolve_conversation_language
    vm = body.voice_meta or {}
    raw_asr_conf = vm.get("asr_confidence")
    asr_conf = float(raw_asr_conf) if isinstance(raw_asr_conf, (int, float)) else None

    client_history = [h.model_dump() for h in body.history]
    if store:
        prior_rows = list(await db.scalars(select(Message).where(Message.session_id == sid).order_by(Message.created_at)))
        stored = [{"role": m.role, "content": cu.cipher.dec(m.content_enc),
                   **({"structured": m.structured} if m.role == "assistant" and m.structured else {})} for m in prior_rows]
        # Edit / Answer again sends the thread still on screen. Drop the saved tail so the model does not
        # answer the question the person just replaced, and so the old copy is not stored a second time.
        if body.replace_history and _history_prefix(client_history, stored):
            drop = [m.id for m in prior_rows[len(client_history):]]
            if drop:
                await db.execute(delete(Message).where(Message.id.in_(drop)))
                await db.commit()
            history = stored[:len(client_history)]
        else:
            history = stored or client_history
    else:
        history = client_history

    # Conversation Language Lock: enforce session language lock across text & voice (persists even when store=False).
    from app.services.language_consistency import LanguageConsistencyService

    lang_svc = LanguageConsistencyService()
    prior_locked_lang = s.lang if history else lang_svc.get_locked(sid)
    lang_decision = lang_svc.resolve(
        text,
        session_id=sid,
        db_locked_lang=prior_locked_lang,
        ui_lang=body.lang,
        explicit_preferred_lang=("en" if body.force_english else body.preferred_response_language),
        asr_confidence=asr_conf,
    )
    lang_res = lang_decision.resolution
    effective_lang = lang_decision.active_language
    if store and (not history or lang_decision.switched) and s.lang != effective_lang:
        s.lang = effective_lang
        await db.commit()
    clinical_text = lang_svc.normalize_for_clinical(text, lang_decision)

    async def gen():
        full_cloud_allowed.set(allow_full)
        yield _sse("status", {"stage": "reading_attachment" if attachments else "thinking"})
        async with SessionLocal() as wdb:  # request-scoped session is closed once streaming starts
            if attachments:
                # A medicine strip / bottle: "what is this?", "is this what the doctor gave?" → salt + story
                structured = None
                if MED_PHOTO_Q.search(text):
                    structured = await medicine_photo_reply(wdb, cu, attachments[0][1], text, effective_lang, model_pref)
                if structured is None:
                    att_kind = await detect_attachment(attachments[0][1])
                    if att_kind == "photo":
                        structured = await medicine_photo_reply(wdb, cu, attachments[0][1], text, effective_lang, model_pref)
                    if structured is None:
                        structured = attachment_reply(att_kind, attachments[0][0], effective_lang)
                if structured is not None:
                    reply = structured["reply"]
                    payload = {"reply": reply, "structured": structured, "citations": [], "disclaimer": "",
                               "stored": store, "language": effective_lang, "language_meta": lang_decision.as_dict()}
                    if store:
                        wdb.add(Message(session_id=sid, role="user", content_enc=cu.cipher.enc(text), lang=effective_lang))
                        m = Message(session_id=sid, role="assistant", content_enc=cu.cipher.enc(reply), lang=effective_lang,
                                    structured=structured, citations=[], safety={})
                        wdb.add(m)
                        await wdb.commit()
                        payload["message_id"] = m.id
                    yield _sse("answer", payload)
                    yield _sse("done", {})
                    return
                yield _sse("status", {"stage": "thinking"})
            cancel_ev: asyncio.Event | None = None
            if body.generation_id:
                from app.clinical.persistence import get_or_create_encounter
                from app.conversation.state import conversation_orchestrator
                enc_pre = await get_or_create_encounter(wdb, cu, chat_session_id=sid, channel=body.channel, language=body.lang)
                await wdb.commit()
                ok_bind, cancel_ev = conversation_orchestrator.bind_generation(enc_pre.id, cu.id, body.generation_id)
                if not ok_bind:
                    yield _sse("cancelled", {"generation_id": body.generation_id})
                    yield _sse("done", {})
                    return
            # Family Health Memory: resolve mentioned relative (e.g. 'Amar baba') and isolate their profile
            from app.clinical.family_memory import (
                get_member_current_user,
                get_member_memory_summary,
                resolve_family_member,
                update_member_health_from_text,
            )
            from app.clinical.pipeline_12layer import run_12layer_clinical_pipeline
            from app.db import scope_to

            active_cu = cu
            family_ctx: dict | None = None
            try:
                fam_res = await resolve_family_member(
                    wdb, cu, text, lang=effective_lang, auto_create_if_missing=store
                )
                if fam_res.matched and fam_res.member_id:
                    mcu = await get_member_current_user(wdb, cu, fam_res.member_id)
                    if mcu is not None:
                        active_cu = mcu
                        if store:
                            await update_member_health_from_text(wdb, active_cu, text, source=body.channel)
                        family_ctx = await get_member_memory_summary(
                            wdb, active_cu, name=fam_res.member_name or "", relation=fam_res.relation or ""
                        )
                        await scope_to(wdb, cu.id, cu.account_id)
                elif store and body.context_scope == "record":
                    await update_member_health_from_text(wdb, cu, text, source=body.channel)
            except Exception:  # noqa: BLE001 — family memory enrichment must never block chat
                log.exception("family memory resolution failed")

            # Patient Identification & Correction (name/age extraction with consent-safe storage)
            from app.services.adaptive_questions import AdaptiveQuestionService
            from app.services.consultation_state_machine import ConsultationStage, consultation_state_machine
            from app.services.emergency_triage import EmergencyTriageService
            from app.services.patient_profile import PatientProfileService, PatientSnapshot
            from app.services.patient_summary import PatientSummaryService, build_7_sections
            from app.services.voice_text_sync import VoiceTextSynchronizationService

            prof_svc = PatientProfileService()
            c_state = consultation_state_machine.get_or_create(sid)
            was_unlocked = c_state.recommendations_unlocked
            patient_snap = PatientSnapshot(user_id=active_cu.id, preferred_lang=effective_lang)
            id_meta: dict = {"updated": [], "is_correction": False, "pure_identity_turn": False}
            try:
                await scope_to(wdb, active_cu.id, active_cu.account_id)
                patient_snap, id_meta = await prof_svc.apply_identity_from_turn(
                    wdb, active_cu, text, session_id=sid, allow_persist=store,
                    pending_field=c_state.pending_profile_field,
                )
                await scope_to(wdb, cu.id, cu.account_id)
            except Exception:  # noqa: BLE001
                log.exception("patient identity extraction failed")
                await scope_to(wdb, cu.id, cu.account_id)

            from app.llm import intent as intent_mod
            from app.safety.red_flags import check as check_red_flag
            recent_u = [h["content"] for h in history if h.get("role") == "user"][-2:]
            em_pre = EmergencyTriageService().assess(
                text, lang=effective_lang, age_years=patient_snap.age,
                recent_turns=recent_u,
            )
            rf_pre = check_red_flag(" ".join(recent_u + [text]), effective_lang, patient_snap.age)
            any_emergency_pre = bool(em_pre.triggered or (rf_pre.triggered and rf_pre.kind in ("emergency", "mental_health")))
            missing_prof = prof_svc.missing_profile_fields(patient_snap)
            is_fixed_conv = bool(intent_mod._phrase_intent(text))
            is_symptom_like = bool(
                c_state.pending_profile_field
                or re.search(
                    r"(?i)\b(?:pain|ache|fever|cough|cold|headache|throat|sore|dizzy|vomit|nausea|rash|itch|bleed|weak|tired|swell|diarrhea|constipation|acidity|burn|infection)\b|"
                    r"ব্যথা|জ্বর|কাশি|গলা|সর্দি|মাথা|বমি|শ্বাস|ফোলা|দুর্বল|পায়খানা|পায়খানা|চুলকানি|র‍্যাশ|दर्द|बुखार|खांसी|सिर|उल्टी|पेट",
                    text or "",
                )
            )
            need_profile_gate = (
                orchestrator.CHAT_DIFFERENTIAL
                and not any_emergency_pre
                and not images
                and not context
                and not was_unlocked
                and not is_fixed_conv
                and not body.generation_id
                and is_symptom_like
                and bool(missing_prof)
            )
            if (id_meta.get("pure_identity_turn") or need_profile_gate) and not any_emergency_pre and not images:
                next_field, q_text = prof_svc.next_missing_profile_question(patient_snap, lang=effective_lang)
                c_state.pending_profile_field = next_field
                if next_field and q_text:
                    if effective_lang == "bn":
                        ack_prefix = "ধন্যবাদ। " if id_meta.get("updated") else ""
                    elif effective_lang == "hi":
                        ack_prefix = "धन्यवाद। " if id_meta.get("updated") else ""
                    else:
                        ack_prefix = "Thank you. " if id_meta.get("updated") else ""
                    id_reply = f"{ack_prefix}{q_text}".strip()
                    stage_after = consultation_state_machine.transition_turn(sid, is_identity_only=True, has_missing_profile=True)
                else:
                    c_state.pending_profile_field = None
                    if effective_lang == "bn":
                        id_reply = f"ধন্যবাদ, {patient_snap.name or ''}—আপনার প্রাথমিক তথ্য সংরক্ষিত হয়েছে। {prof_svc.ready_prompt_for('bn')}".strip()
                    elif effective_lang == "hi":
                        id_reply = f"धन्यवाद, {patient_snap.name or ''}—आपकी जानकारी सहेज ली गई है। {prof_svc.ready_prompt_for('hi')}".strip()
                    else:
                        id_reply = f"Thank you, {patient_snap.name or ''} — I have noted your details. {prof_svc.ready_prompt_for('en')}".strip()
                    c_state.advance_to(ConsultationStage.ACTIVE_CONSULTATION)
                    stage_after = c_state.as_dict()

                id_structured: dict = {
                    "kind": "general_health",
                    "reply": id_reply,
                    "tips": [],
                    "follow_up_questions": [q_text] if q_text else [],
                    "recommendation_ready": False,
                    "spoken_response": id_reply,
                    "spoken_segments": [{"section_id": "acknowledgement", "text": id_reply}],
                    "sync_sections": [{"id": "acknowledgement", "title": "Patient Identification", "screen_text": id_reply, "spoken_text": id_reply, "items": []}],
                    "language": effective_lang,
                    "language_meta": lang_decision.as_dict(),
                }
                id_payload: dict = {
                    "reply": id_reply, "structured": id_structured, "citations": [], "disclaimer": "",
                    "provider": "sushrut_profile", "model": "deterministic", "tier": "L", "speed": "instant",
                    "auto": model_pref == "auto", "latency_ms": 1, "stored": store,
                    "language": effective_lang, "language_meta": lang_decision.as_dict(),
                    "patient_profile": patient_snap.as_dict(),
                    "compact_summary": None,
                    "assessment_panel": None,
                    "recommendation_ready": False,
                    "consultation_stage": stage_after,
                }
                if store:
                    wdb.add(Message(session_id=sid, role="user", content_enc=cu.cipher.enc(text), lang=effective_lang))
                    m = Message(session_id=sid, role="assistant", content_enc=cu.cipher.enc(id_reply), lang=effective_lang,
                                structured=id_structured, citations=[], safety={})
                    wdb.add(m)
                    await wdb.commit()
                    id_payload["message_id"] = m.id
                yield _sse("answer", id_payload)
                yield _sse("done", {})
                return

            c_state.pending_profile_field = None
            if orchestrator.CHAT_DIFFERENTIAL and not em_pre.triggered and c_state.followups_asked > c_state.followups_answered:
                consultation_state_machine.record_followup_answer(sid)

            member_context = ""
            if family_ctx:
                known_bits = []
                if family_ctx.get("age") is not None:
                    known_bits.append(f"Age {family_ctx['age']}")
                if family_ctx.get("conditions"):
                    known_bits.append("Conditions: " + ", ".join(family_ctx["conditions"]))
                if family_ctx.get("latest_bp"):
                    known_bits.append(f"Latest BP: {family_ctx['latest_bp']}")
                if family_ctx.get("medications"):
                    known_bits.append("Meds: " + ", ".join(family_ctx["medications"]))
                if family_ctx.get("allergies"):
                    known_bits.append("Allergies: " + ", ".join(family_ctx["allergies"]))
                member_context = (
                    f"[Selected Patient: {family_ctx.get('name')} ({family_ctx.get('relation')}). "
                    + "; ".join(known_bits)
                    + ". Do NOT re-ask already known age/conditions/allergies unless confirming a change.]"
                )
            combined_context = "\n".join(x for x in (member_context, context) if x).strip()

            drafts: asyncio.Queue[str] = asyncio.Queue()
            await scope_to(wdb, active_cu.id, active_cu.account_id)
            task = asyncio.create_task(orchestrator.answer(wdb, active_cu, clinical_text, lang=effective_lang, session_id=sid, history=history,
                                                           images=images or None, allow_cloud=allow_cloud,
                                                           context=combined_context, hint_kind=hint_kind,
                                                           on_delta=drafts.put_nowait, model_pref=model_pref,
                                                           spoken_prefix=body.spoken_prefix,
                                                           force_english=body.force_english,
                                                           use_record=body.context_scope == "record",
                                                           channel=body.channel,
                                                           patient_snapshot=patient_snap))
            try:
                # "delta" carries the whole reply written so far (a draft; "answer" replaces it). Only the newest
                # draft is sent, at most ~8 times a second; a comment line keeps quiet connections open.
                while not task.done():
                    if cancel_ev is not None and cancel_ev.is_set():
                        task.cancel()
                        yield _sse("cancelled", {"generation_id": body.generation_id})
                        yield _sse("done", {})
                        return
                    try:
                        latest = await asyncio.wait_for(drafts.get(), timeout=0.5 if cancel_ev is not None else 2.0)
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
            except asyncio.CancelledError:
                yield _sse("cancelled", {"generation_id": body.generation_id or ""})
                yield _sse("done", {})
                return
            except ProviderUnavailable as e:
                yield _sse("error", {"message": str(e), "hint": e.hint, "busy": isinstance(e, ModelBusy)})
                return
            if body.generation_id:
                from app.clinical.persistence import get_or_create_encounter
                from app.conversation.state import conversation_orchestrator
                enc_chk = await get_or_create_encounter(wdb, cu, chat_session_id=sid, channel=body.channel, language=body.lang)
                if not conversation_orchestrator.is_generation_current(enc_chk.id, cu.id, body.generation_id):
                    yield _sse("cancelled", {"generation_id": body.generation_id})
                    yield _sse("done", {})
                    return
            await scope_to(wdb, cu.id, cu.account_id)
            if r.red_flag.get("triggered"):
                yield _sse("red_flag", r.red_flag)
            cum_ctx = AdaptiveQuestionService().build_context(history, clinical_text, lang=effective_lang)
            st_dict = r.structured if isinstance(r.structured, dict) else {}
            is_emergency = bool(r.red_flag.get("triggered") and r.red_flag.get("kind") in ("emergency", "mental_health"))
            rec_ready = bool(
                is_emergency
                or not orchestrator.CHAT_DIFFERENTIAL
                or (r.kind != "clarify" and st_dict.get("recommendation_ready", True))
            )
            assessment_panel = None
            compact_summary = None
            sum_svc = PatientSummaryService()
            if rec_ready:
                assessment_panel = run_12layer_clinical_pipeline(
                    cum_ctx.merged_clinical_text or clinical_text,
                    lang=effective_lang,
                    patient_id=active_cu.id,
                    encounter_id=sid,
                    age_years=family_ctx.get("age") if family_ctx else patient_snap.age,
                    comorbidities=family_ctx.get("conditions", []) if family_ctx else patient_snap.conditions,
                    allergies=family_ctx.get("allergies", []) if family_ctx else patient_snap.allergies,
                )
            if isinstance(r.structured, dict):
                r.structured["language_meta"] = lang_decision.as_dict()
                r.structured["recommendation_ready"] = rec_ready
                if family_ctx:
                    r.structured["family_context"] = family_ctx
            if rec_ready:
                await scope_to(wdb, active_cu.id, active_cu.account_id)
                compact_summary = await sum_svc.build_compact_summary(
                    wdb,
                    active_cu,
                    session_id=sid,
                    latest_user_text=text,
                    latest_structured=r.structured if isinstance(r.structured, dict) else None,
                    latest_panel=assessment_panel,
                    lang=effective_lang,
                )
                await scope_to(wdb, cu.id, cu.account_id)
                if isinstance(r.structured, dict):
                    r.structured["sections_7"] = build_7_sections(
                        patient=patient_snap.as_dict(),
                        compact_summary=compact_summary,
                        structured=r.structured,
                        lang=effective_lang,
                    )
            stage_state = consultation_state_machine.transition_turn(
                sid,
                emergency=is_emergency,
                symptomatic=(r.kind == "symptoms" or r.kind == "clarify"),
                followup_asked=bool(r.kind == "clarify" or not rec_ready),
                complaints=[cum_ctx.chief_complaint] if cum_ctx.chief_complaint else [],
                has_investigations=bool(st_dict.get("recommended_tests")) if rec_ready else False,
                has_specialist=bool(st_dict.get("recommended_specialist")) if rec_ready else False,
                has_summary=bool(compact_summary),
                recommendation_ready=rec_ready,
                new_symptom_after_final=bool(was_unlocked and (r.kind == "symptoms" or r.kind == "clarify")),
            )
            payload = {"reply": r.reply, "structured": r.structured, "citations": r.citations,
                       "disclaimer": r.disclaimer, "provider": r.provider, "model": r.model, "tier": r.tier, "speed": r.speed, "auto": model_pref == "auto",
                       "latency_ms": r.latency_ms, "stored": store, "language": effective_lang, "language_meta": lang_decision.as_dict(),
                       "family_context": family_ctx, "patient_profile": patient_snap.as_dict(),
                       "assessment_panel": assessment_panel, "compact_summary": compact_summary,
                       "recommendation_ready": rec_ready,
                       "consultation_stage": stage_state}
            if store:
                wdb.add(Message(session_id=sid, role="user", content_enc=cu.cipher.enc(text), lang=effective_lang))
                m = Message(session_id=sid, role="assistant", content_enc=cu.cipher.enc(r.reply), lang=effective_lang,
                            structured=r.structured, citations=r.citations, safety={**r.red_flag, "warnings": r.warnings},
                            provider=r.provider, model=r.model, privacy_tier=r.tier, latency_ms=r.latency_ms)
                wdb.add(m)
                await wdb.commit()
                payload["message_id"] = m.id
                if (r.kind or "") not in MEMORY_SKIP_KINDS:
                    try:
                        from app.clinical.conversation_memory import save_conversation_summary
                        payload["conversation_summary"] = await save_conversation_summary(
                            wdb,
                            active_cu,
                            conversation_id=sid,
                            user_text=cum_ctx.merged_clinical_text or text,
                            channel=body.channel,
                            lang=effective_lang,
                            pipeline_result=assessment_panel,
                        )
                        if compact_summary:
                            await sum_svc.save_if_consented(
                                wdb, active_cu, session_id=sid, summary=compact_summary, channel=body.channel, lang=effective_lang
                            )
                        await scope_to(wdb, cu.id, cu.account_id)
                    except Exception:  # noqa: BLE001
                        log.exception("auto conversation summary save failed")
                # Health memory: values said in this turn are saved now ("Saving: 101 °F · 2 days", with undo);
                # symptoms, triggers and medicine responses follow from a small model in the background.
                if body.context_scope == "record" and r.structured is not None and (r.kind or "") not in MEMORY_SKIP_KINDS:
                    await scope_to(wdb, active_cu.id, active_cu.account_id)
                    payload["saved_facts"] = await remember_turn(wdb, active_cu, text, r.english, sid)
                    await scope_to(wdb, cu.id, cu.account_id)
                if body.context_scope == "record" or body.channel == "voice":
                    try:
                        await scope_to(wdb, active_cu.id, active_cu.account_id)
                        payload["clinical"] = await _persist_clinical_turn(
                            wdb, active_cu, sid=sid, body=body, user_text=text, result=r
                        )
                        await scope_to(wdb, cu.id, cu.account_id)
                        if rec_ready and payload["clinical"].get("followup_question") and isinstance(r.structured, dict):
                            fq_q = payload["clinical"]["followup_question"]["question"]
                            existing_fqs = list(r.structured.get("follow_up_questions") or [])
                            if fq_q and fq_q not in existing_fqs:
                                r.structured["follow_up_questions"] = [fq_q] + existing_fqs[:(0 if body.channel == "voice" else 2)]
                                VoiceTextSynchronizationService().synchronize(
                                    lang=effective_lang,
                                    structured=r.structured,
                                    specialty=r.structured.get("recommended_specialist"),
                                    investigations={"items": r.structured.get("recommended_tests") or []},
                                    diet=r.structured.get("diet_and_lifestyle"),
                                    followup_question=fq_q,
                                    channel=body.channel,
                                )
                    except Exception:  # noqa: BLE001 — clinical persistence never breaks delivery of answer
                        log.exception("clinical turn persistence failed")
                        await scope_to(wdb, cu.id, cu.account_id)
            yield _sse("answer", payload)
            yield _sse("done", {})
            if (store and body.context_scope == "record" and r.structured is not None
                    and (r.kind or "") not in MEMORY_SKIP_KINDS):
                asyncio.create_task(remember_turn_later(active_cu, r.english or text, sid))

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
