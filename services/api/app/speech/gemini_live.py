"""Gemini Live API gateway.

The browser streams 16 kHz PCM to our WebSocket. This module holds the socket to Google, so the API key
never reaches the page. Talk live may call the existing health answer; it does not answer from its own
medical knowledge.
"""
from __future__ import annotations

import base64
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from app.config import get_settings
from app.safety.red_flags import check

log = logging.getLogger("sushrut.gemini_live")

LIVE_URL = ("wss://generativelanguage.googleapis.com/ws/"
            "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent")

_BCP = {
    "en": "en-IN", "hi": "hi-IN", "bn": "bn-IN", "ta": "ta-IN", "te": "te-IN", "mr": "mr-IN",
    "gu": "gu-IN", "kn": "kn-IN", "ml": "ml-IN", "or": "or-IN", "pa": "pa-IN", "as": "as-IN", "ur": "ur-IN",
}

TALK_INSTRUCTION = (
    "You are the speaking voice of SushrutAI, a careful health-information assistant for people in India. "
    "Respond naturally in the same language or code-switched style as the speaker (Bengali bn-IN, Hindi hi-IN, Hinglish, Banglish, or Indian English). "
    "Wait patiently during brief pauses so the person can finish speaking; if they speak while you are talking, stop immediately and listen. "
    "When the person asks about their health, call answer_health_question with their question and then speak "
    "only the text that tool returns. Do not invent a diagnosis, a medicine, or a dose. "
    "If that text says to call 108 or 112, say that and stop."
)

_ANSWER_TOOL = {
    "functionDeclarations": [{
        "name": "answer_health_question",
        "description": "Return SushrutAI's checked health answer. Speak only the result text.",
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string", "description": "The person's question, in their words."}},
            "required": ["question"],
        },
    }]
}


def resolve_model(mode: str, model: str | None = None) -> str:
    """The Live model id for a session. Unknown talk choices use Gemini 3.8 Live."""
    s = get_settings()
    if mode == "transcribe":
        return s.gemini_transcribe_live_model
    if mode == "translate":
        return s.gemini_translate_live_model
    if (model or "") in ("flash", "gemini_flash", s.gemini_flash_live_model):
        return s.gemini_flash_live_model
    return s.gemini_live_model


_LANG_LABELS = {
    "en": "Indian English (en-IN)",
    "hi": "Hindi (hi-IN)",
    "bn": "Bengali (bn-IN)",
    "ta": "Tamil (ta-IN)",
    "te": "Telugu (te-IN)",
    "mr": "Marathi (mr-IN)",
    "gu": "Gujarati (gu-IN)",
    "kn": "Kannada (kn-IN)",
    "ml": "Malayalam (ml-IN)",
    "or": "Odia (or-IN)",
    "pa": "Punjabi (pa-IN)",
    "as": "Assamese (as-IN)",
    "ur": "Urdu (ur-IN)",
}


def build_talk_instruction(lang: str = "en", patient_context: str = "") -> str:
    """Build system instruction for Gemini Live with strict Conversation Language Lock."""
    clean = (lang or "en").split("-")[0].lower()
    if clean in ("auto", "app", ""):
        lock_clause = (
            "Detect the speaker's language on the first turn and lock onto it for the rest of the conversation. "
            "If they speak Banglish (Bengali in Roman letters), lock onto Bengali (bn-IN) and NEVER switch to Hindi. "
            "Only switch languages if the user explicitly asks to change languages."
        )
    else:
        label = _LANG_LABELS.get(clean, clean)
        lock_clause = (
            f"ACTIVE CONVERSATION LANGUAGE LOCK: {label}. "
            f"You MUST speak and respond strictly in {label}. "
            "Do NOT switch to Hindi, English, or any other language mid-conversation, and do NOT insert "
            "unnecessary English translations into Bengali or Hindi responses (English medical test/specialty terms may be briefly clarified in the active language). "
            "Only switch language if the user explicitly requests a language change."
        )
    patient_clause = f" KNOWN PATIENT CONTEXT: {patient_context}" if patient_context else ""
    return (
        "You are the speaking voice of SushrutAI, a patient, attentive, doctor-like health-information assistant for people in India. "
        f"{lock_clause}{patient_clause} "
        "FOLLOW THIS PROACTIVE 10-STEP CONSULTATION WORKFLOW: "
        "1. Greet the patient ONCE on session start with the exact SushrutAI greeting ('আমি Sushrut AI, কীভাবে আপনাকে সাহায্য করতে পারি?' in Bengali or 'Hello, I am SUSHRUT AI. How can I help you today?' in English). Never repeat the greeting in the same session. "
        "2. Collect Name, Age, and Gender one at a time BEFORE starting routine medical consultation (skip any already-saved fields; if declined or unknown, mark unavailable and proceed). "
        "3. Listen patiently! Allow long pauses while the patient thinks or remembers symptoms; NEVER interrupt them prematurely. "
        "4. For non-emergency symptoms, NEVER give final recommendations, possible conditions, doctor referrals, or test lists after only the first symptom. Ask at least 2 relevant follow-up questions (ONE at a time, never repeating answered topics) first. "
        "5. ALWAYS call answer_health_question with the patient's input and speak only what it returns (follow-up question during ACTIVE_CONSULTATION, or the 7 structured sections once recommendations are unlocked). "
        "6. If emergency signs (108 / 112) are present, speak the emergency warning immediately and stop routine questioning."
    )


def proactive_greeting_turn(lang: str = "en", onboarding_prompt: str | None = None) -> str:
    """Trigger Gemini Live to proactively open the consultation ONCE on session ready with exact SushrutAI greeting."""
    from app.services.patient_profile import PatientProfileService

    clean = (lang or "en").split("-")[0].lower()
    svc = PatientProfileService()
    exact_greet = svc.greeting_for(clean)
    if onboarding_prompt:
        prompt = f"Session started. Speak this exact opening warmly and naturally, then listen patiently: {onboarding_prompt}"
    elif clean == "bn":
        prompt = f"Session started. Speak this exact Bengali greeting warmly and naturally: {exact_greet} এরপর প্রয়োজনে জিজ্ঞাসা করুন: আপনি কি নিজের জন্য জানতে চাইছেন, নাকি পরিবারের অন্য কারও জন্য?"
    elif clean == "hi":
        prompt = f"Session started. Speak this exact Hindi greeting warmly and naturally: {exact_greet}"
    else:
        prompt = f"Session started. Speak this exact greeting warmly and naturally: {exact_greet}"
    return json.dumps({"clientContent": {"turns": [{"role": "user", "parts": [{"text": prompt}]}], "turnComplete": True}})


def setup_message(
    mode: str,
    model: str,
    *,
    lang: str = "en",
    target_lang: str = "en",
    silence_ms: int = 3500,
    patient_context: str = "",
) -> dict:
    """The first frame sent to Gemini. mode is transcribe, translate, or talk."""
    body: dict[str, Any] = {"setup": {"model": f"models/{model}"}}
    if mode == "transcribe":
        codes = [] if lang in ("auto", "app", "") else [_BCP.get(lang, lang)]
        body["setup"]["generationConfig"] = {"responseModalities": ["TEXT"]}
        body["setup"]["inputAudioTranscription"] = {"languageCodes": codes}
    elif mode == "translate":
        target = "en" if target_lang in ("auto", "app", "") else target_lang.split("-")[0]
        body["setup"]["generationConfig"] = {
            "responseModalities": ["AUDIO"],
            "translationConfig": {"targetLanguageCode": target, "echoTargetLanguage": False},
        }
        body["setup"]["inputAudioTranscription"] = {}
        body["setup"]["outputAudioTranscription"] = {}
    else:
        clean = (lang or "en").split("-")[0].lower()
        in_codes = [] if clean in ("auto", "app", "") else list(dict.fromkeys([_BCP.get(clean, clean), "en-IN"]))
        body["setup"]["generationConfig"] = {"responseModalities": ["AUDIO"]}
        body["setup"]["systemInstruction"] = {"parts": [{"text": build_talk_instruction(lang, patient_context)}]}
        body["setup"]["tools"] = [_ANSWER_TOOL]
        body["setup"]["inputAudioTranscription"] = {"languageCodes": in_codes} if in_codes else {}
        body["setup"]["outputAudioTranscription"] = {}
        # Patient-friendly inactivity detection so slow/pausing speakers are not cut off prematurely.
        body["setup"]["realtimeInputConfig"] = {
            "automaticActivityDetection": {
                "disabled": False,
                "silenceDurationMs": max(1500, min(int(silence_ms), 8000)),
            }
        }
    return body


def audio_end() -> str:
    return json.dumps({"realtimeInput": {"audioStreamEnd": True}})


def audio_frame(pcm: bytes) -> str:
    return json.dumps({"realtimeInput": {"audio": {
        "mimeType": "audio/pcm;rate=16000",
        "data": base64.b64encode(pcm).decode(),
    }}})


def tool_response(call_id: str, name: str, text: str) -> str:
    return json.dumps({"toolResponse": {"functionResponses": [{
        "id": call_id, "name": name, "response": {"result": text},
    }]}})


def emergency_turn(text: str) -> str:
    """Tell the live model to speak the emergency line and not continue the question."""
    return json.dumps({"clientContent": {"turns": [{"role": "user", "parts": [{
        "text": "Stop. Say only this, then stop: " + text[:800],
    }]}], "turnComplete": True}})


@dataclass
class LiveEvent:
    json: dict | None = None
    audio: bytes | None = None


def _text_of(node: Any) -> str:
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        return str(node.get("text") or "")
    return ""


def _lang_of(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    code = str(node.get("languageCode") or node.get("language_code") or "")
    return code.split("-")[0].lower()


def parse_server_message(msg: dict) -> list[LiveEvent]:
    """Gemini Live frames become events the browser can show. Audio is raw PCM bytes."""
    if not isinstance(msg, dict):
        return []
    err = msg.get("error")
    if err:
        message = err.get("message") if isinstance(err, dict) else str(err)
        return [LiveEvent(json={"type": "error", "message": str(message)[:300]})]
    if msg.get("setupComplete") is not None:
        return [LiveEvent(json={"type": "ready"})]
    out: list[LiveEvent] = []
    sc = msg.get("serverContent") or msg.get("server_content") or {}
    interim = sc.get("interimInputTranscription") or sc.get("interim_input_transcription")
    if interim:
        out.append(LiveEvent(json={"type": "interim", "text": _text_of(interim)}))
    final = sc.get("inputTranscription") or sc.get("input_transcription")
    if final:
        text = _text_of(final)
        heard_lang = _lang_of(final)
        final_ev = {"type": "final", "text": text}
        heard_ev = {"type": "input_transcript", "text": text}
        if heard_lang:
            final_ev["lang"] = heard_lang
            heard_ev["lang"] = heard_lang
        out.append(LiveEvent(json=final_ev))
        out.append(LiveEvent(json=heard_ev))
    spoken = sc.get("outputTranscription") or sc.get("output_transcription")
    if spoken:
        out.append(LiveEvent(json={"type": "output_transcript", "text": _text_of(spoken)}))
    turn = (sc.get("modelTurn") or sc.get("model_turn") or {})
    for part in turn.get("parts") or []:
        inline = part.get("inlineData") or part.get("inline_data") or {}
        data = inline.get("data")
        if data:
            try:
                out.append(LiveEvent(audio=base64.b64decode(data)))
            except (ValueError, TypeError):
                log.warning("dropped a live audio chunk that was not base64")
        if part.get("text"):
            out.append(LiveEvent(json={"type": "output_transcript", "text": part["text"]}))
    tc = msg.get("toolCall") or msg.get("tool_call") or {}
    for call in tc.get("functionCalls") or tc.get("function_calls") or []:
        args = call.get("args") or {}
        out.append(LiveEvent(json={
            "type": "tool_call",
            "id": call.get("id") or "",
            "name": call.get("name") or "",
            "question": (args.get("question") if isinstance(args, dict) else "") or "",
        }))
    if sc.get("interrupted"):
        out.append(LiveEvent(json={"type": "interrupted"}))
    if sc.get("turnComplete") or sc.get("turn_complete"):
        out.append(LiveEvent(json={"type": "turn_complete"}))
    if sc.get("generationComplete") or sc.get("generation_complete"):
        out.append(LiveEvent(json={"type": "generation_complete"}))
    va = msg.get("voiceActivity") or msg.get("voice_activity")
    if isinstance(va, dict):
        vt = str(va.get("voiceActivityType") or va.get("voice_activity_type") or "").upper()
        if "START" in vt:
            out.append(LiveEvent(json={"type": "vad_start"}))
        elif "END" in vt:
            out.append(LiveEvent(json={"type": "vad_end"}))
    sr = msg.get("sessionResumptionUpdate") or msg.get("session_resumption_update")
    if isinstance(sr, dict) and (sr.get("newHandle") or sr.get("new_handle")):
        out.append(LiveEvent(json={"type": "session_resumption", "handle": str(sr.get("newHandle") or sr.get("new_handle"))}))
    return out


def emergency_for(text: str, lang: str) -> dict | None:
    """Emergency and mental-health lines stop the turn. Urgent wording does not."""
    rf = check(text or "", lang if lang not in ("auto", "app") else "en")
    if not rf.triggered or rf.kind not in ("emergency", "mental_health"):
        return None
    return {"type": "red_flag", "text": "\n\n".join(rf.messages), "safety": rf.as_dict(), "skip_tool": True}


@dataclass
class LiveSession:
    """One Gemini Live socket. Tests pass a fake socket with send/recv."""

    ws: Any
    mode: str
    lang: str = "en"
    blocked: bool = False
    _pending_audio: list[bytes] = field(default_factory=list)
    ready: bool = False

    async def send_setup(
        self,
        model: str,
        *,
        target_lang: str = "en",
        silence_ms: int = 3500,
        patient_context: str = "",
    ) -> None:
        await self.ws.send(json.dumps(
            setup_message(
                self.mode,
                model,
                lang=self.lang,
                target_lang=target_lang,
                silence_ms=silence_ms,
                patient_context=patient_context,
            )
        ))

    async def send_audio(self, pcm: bytes) -> None:
        if not pcm:
            return
        if not self.ready:
            self._pending_audio.append(pcm)
            return
        await self.ws.send(audio_frame(pcm))

    async def _flush(self) -> None:
        self.ready = True
        for pcm in self._pending_audio:
            await self.ws.send(audio_frame(pcm))
        self._pending_audio.clear()

    async def send_text_frame(self, raw: str) -> None:
        await self.ws.send(raw)

    async def messages(self) -> AsyncIterator[LiveEvent]:
        async for raw in self.ws:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", "replace")
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            events = parse_server_message(msg)
            if any(e.json and e.json.get("type") == "ready" for e in events):
                await self._flush()
            for ev in events:
                yield ev


async def checked_answer_detail(
    token: str,
    profile_id: str | None,
    question: str,
    lang: str,
    *,
    session_id: str | None = None,
    history: list[dict[str, str]] | None = None,
    locked_lang: str | None = None,
    active_member_id: str | None = None,
) -> dict[str, Any]:
    """The Talk live tool with full structured CDSS payload, family memory, language lock, and spoken_response."""
    from app.clinical.family_memory import (
        get_member_current_user,
        get_member_memory_summary,
        resolve_family_member,
        update_member_health_from_text,
    )
    from app.clinical.pipeline_12layer import run_12layer_clinical_pipeline
    from app.db import SessionLocal, scope_to
    from app.llm.orchestrator import answer
    from app.llm.providers import full_cloud_allowed
    from app.models import ChatSession, Message
    from app.security.auth import authenticate_token
    from app.security.settings import cloud_allowed
    from app.speech.normalize import normalize_banglish_hinglish, resolve_conversation_language

    from app.services.language_consistency import LanguageConsistencyService
    from app.services.patient_profile import PatientProfileService
    from app.services.patient_summary import PatientSummaryService

    question = (question or "").strip()
    if not question:
        return {"reply": "I did not catch a question.", "spoken_response": "I did not catch a question.", "language": locked_lang or "en", "structured": None}
    ui_lang = "en" if lang in ("auto", "app", "") else lang
    lang_decision = LanguageConsistencyService().resolve(
        question,
        session_id=session_id,
        db_locked_lang=locked_lang or (None if lang in ("auto", "app", "") else lang),
        ui_lang=ui_lang,
    )
    lang_res = lang_decision.resolution
    use_lang = lang_decision.active_language
    clinical_q = normalize_banglish_hinglish(question) if (lang_res.banglish_detected or lang_res.hinglish_detected) else question
    try:
        async with SessionLocal() as db:
            cu = await authenticate_token(token, db, x_profile_id=profile_id)
            s = get_settings()
            can_cloud = bool(s.gemini_api_key) and (
                await cloud_allowed(db, cu.account_id, "cloud_llm_live")
                or await cloud_allowed(db, cu.account_id, "cloud_llm_full")
            )
            pref = "gemini_fast" if can_cloud else "balanced"

            # Family Health Memory resolution (switches if new relative mentioned, else reuses active_member_id)
            active_cu = cu
            resolved_member_id = active_member_id
            family_ctx: dict | None = None
            member_context = ""
            try:
                fam_res = await resolve_family_member(db, cu, question, lang=use_lang, auto_create_if_missing=True)
                if fam_res.matched and fam_res.member_id:
                    resolved_member_id = fam_res.member_id
                if resolved_member_id:
                    mcu = await get_member_current_user(db, cu, resolved_member_id)
                    if mcu is not None:
                        active_cu = mcu
                        await update_member_health_from_text(db, active_cu, question, source="voice")
                        family_ctx = await get_member_memory_summary(
                            db, active_cu, name=(fam_res.member_name if fam_res.matched else "") or "", relation=(fam_res.relation if fam_res.matched else "") or ""
                        )
                        await scope_to(db, cu.id, cu.account_id)
                        bits = []
                        if family_ctx.get("age") is not None:
                            bits.append(f"Age {family_ctx['age']}")
                        if family_ctx.get("conditions"):
                            bits.append("Conditions: " + ", ".join(family_ctx["conditions"]))
                        if family_ctx.get("latest_bp"):
                            bits.append(f"Latest BP: {family_ctx['latest_bp']}")
                        if family_ctx.get("medications"):
                            bits.append("Meds: " + ", ".join(family_ctx["medications"]))
                        if family_ctx.get("allergies"):
                            bits.append("Allergies: " + ", ".join(family_ctx["allergies"]))
                        member_context = (
                            f"[Selected Patient: {family_ctx.get('name') or 'Family Member'} ({family_ctx.get('relation') or 'family'}). "
                            + "; ".join(bits)
                            + ". Do NOT re-ask already known age/conditions/allergies.]"
                        )
            except Exception:  # noqa: BLE001
                log.exception("live family memory resolution failed")

            prof_svc = PatientProfileService()
            await scope_to(db, active_cu.id, active_cu.account_id)
            patient_snap, _ = await prof_svc.apply_identity_from_turn(
                db, active_cu, question, session_id=session_id, allow_persist=True
            )

            tok = full_cloud_allowed.set(can_cloud)
            try:
                result = await answer(
                    db, active_cu, clinical_q[:4000],
                    lang=use_lang,
                    history=history or [],
                    allow_cloud=can_cloud,
                    context=member_context,
                    model_pref=pref,
                    channel="voice",
                    patient_snapshot=patient_snap,
                )
            finally:
                full_cloud_allowed.reset(tok)
            await scope_to(db, cu.id, cu.account_id)
            reply = (result.reply or "")[:3500] or "I could not check that just now."
            structured = result.structured if isinstance(result.structured, dict) else None
            spoken = (structured.get("spoken_response") if structured else None) or reply
            merged_q = "\n".join([*(h["content"] for h in (history or []) if h.get("role") == "user"), clinical_q])
            assessment_panel = run_12layer_clinical_pipeline(
                merged_q,
                lang=use_lang,
                patient_id=active_cu.id,
                encounter_id=session_id,
                age_years=family_ctx.get("age") if family_ctx else patient_snap.age,
                comorbidities=family_ctx.get("conditions", []) if family_ctx else patient_snap.conditions,
                allergies=family_ctx.get("allergies", []) if family_ctx else patient_snap.allergies,
            )
            compact_summary = None
            if session_id:
                sess = await db.get(ChatSession, session_id)
                if sess and sess.user_id == cu.id:
                    db.add(Message(session_id=session_id, role="user",
                                   content_enc=cu.cipher.enc(question[:4000]), lang=use_lang))
                    db.add(Message(session_id=session_id, role="assistant",
                                   content_enc=cu.cipher.enc(reply), lang=use_lang,
                                   structured=structured,
                                   provider="gemini_live"))
                    await db.commit()
                    try:
                        from app.clinical.conversation_memory import save_conversation_summary
                        await save_conversation_summary(
                            db,
                            active_cu,
                            conversation_id=session_id,
                            user_text=merged_q,
                            channel="voice",
                            lang=use_lang,
                            pipeline_result=assessment_panel,
                        )
                        sum_svc = PatientSummaryService()
                        await scope_to(db, active_cu.id, active_cu.account_id)
                        compact_summary = await sum_svc.build_compact_summary(
                            db, active_cu, session_id=session_id, latest_user_text=question,
                            latest_structured=structured, latest_panel=assessment_panel, lang=use_lang,
                        )
                        await sum_svc.save_if_consented(
                            db, active_cu, session_id=session_id, summary=compact_summary, channel="voice", lang=use_lang
                        )
                        await scope_to(db, cu.id, cu.account_id)
                    except Exception:  # noqa: BLE001
                        log.exception("live conversation summary save failed")
            return {
                "reply": reply,
                "spoken_response": str(spoken)[:3500],
                "language": use_lang,
                "language_meta": lang_decision.as_dict(),
                "structured": structured,
                "red_flag": result.red_flag,
                "active_member_id": resolved_member_id,
                "family_context": family_ctx,
                "patient_profile": patient_snap.as_dict(),
                "assessment_panel": assessment_panel,
                "compact_summary": compact_summary,
            }
    except Exception:
        log.exception("live answer tool failed")
        fallback = "I could not check that just now. If this feels urgent, call 108 or 112."
        return {"reply": fallback, "spoken_response": fallback, "language": use_lang, "structured": None}


async def checked_answer(
    token: str,
    profile_id: str | None,
    question: str,
    lang: str,
    *,
    session_id: str | None = None,
    history: list[dict[str, str]] | None = None,
) -> str:
    """Backward-compatible wrapper returning spoken_response/reply string."""
    detail = await checked_answer_detail(
        token, profile_id, question, lang, session_id=session_id, history=history
    )
    return detail["spoken_response"]


async def serve(browser: Any, *, token: str, profile_id: str | None) -> None:
    """Browser WebSocket ↔ Gemini Live. Closes with 4401 or 4403 before accept when not allowed."""
    import asyncio

    from fastapi import HTTPException

    from app.db import SessionLocal
    from app.security.auth import authenticate_token
    from app.security.consent import active_purposes
    from app.security.settings import cloud_allowed

    if not token:
        await browser.close(code=4401)
        return
    async with SessionLocal() as db:
        try:
            cu = await authenticate_token(token, db, x_profile_id=profile_id)
        except HTTPException:
            await browser.close(code=4401)
            return
        allowed = ("core_service" in await active_purposes(db, cu.account_id)
                   and await cloud_allowed(db, cu.account_id, "cloud_llm_live"))
    if not allowed:
        await browser.close(code=4403)
        return
    await browser.accept()
    try:
        start = json.loads(await browser.receive_text())
    except (json.JSONDecodeError, RuntimeError):
        await browser.close(code=1003)
        return
    mode = start.get("mode") or "transcribe"
    if mode not in ("transcribe", "translate", "talk"):
        await browser.send_json({"type": "error", "message": "Unknown live mode"})
        await browser.close()
        return
    lang = str(start.get("lang") or "en")
    target = str(start.get("target_lang") or lang)
    session_id = str(start.get("session_id") or "").strip() or None
    silence_ms = int(start.get("silence_ms") or 3500)
    model = resolve_model(mode, start.get("model"))

    onboarding_info: dict[str, Any] = {}
    patient_ctx_text = ""
    if mode == "talk":
        try:
            from app.services.patient_profile import PatientProfileService
            async with SessionLocal() as db:
                cu_init = await authenticate_token(token, db, x_profile_id=profile_id)
                psvc = PatientProfileService()
                snap = await psvc.load_snapshot(db, cu_init, session_id=session_id)
                onboarding_info = psvc.onboarding_prompt(snap, lang=lang)
                bits = []
                if snap.name:
                    bits.append(f"Name={snap.name}")
                if snap.age is not None:
                    bits.append(f"Age={snap.age}")
                if snap.conditions:
                    bits.append("Conditions=" + ", ".join(snap.conditions))
                if snap.allergies:
                    bits.append("Allergies=" + ", ".join(snap.allergies))
                patient_ctx_text = "; ".join(bits)
        except Exception:  # noqa: BLE001
            onboarding_info = {}

    try:
        session = await connect_live(
            mode,
            model,
            lang=lang,
            target_lang=target,
            silence_ms=silence_ms,
            patient_context=patient_ctx_text,
        )
    except Exception as e:
        log.warning("gemini live connect failed: %s", e)
        await browser.send_json({"type": "error", "message": "Gemini Live did not connect. Try again in a minute."})
        await browser.close()
        return

    live_history: list[dict[str, str]] = []
    locked_lang: str | None = None if lang in ("auto", "app", "") else lang.split("-")[0].lower()
    active_member_id: str | None = None
    paused = False
    greeted = False
    active_tool_task: asyncio.Task | None = None

    async def from_browser() -> None:
        nonlocal paused, active_tool_task
        while True:
            msg = await browser.receive()
            if msg.get("type") == "websocket.disconnect":
                return
            if msg.get("bytes"):
                if not paused:
                    await session.send_audio(msg["bytes"])
            elif msg.get("text"):
                try:
                    cmd = json.loads(msg["text"])
                except json.JSONDecodeError:
                    continue
                ctype = cmd.get("type")
                if ctype == "pause":
                    paused = True
                    await browser.send_json({"type": "state", "state": "PAUSED"})
                    continue
                if ctype == "resume":
                    paused = False
                    await browser.send_json({"type": "state", "state": "LISTENING"})
                    continue
                if ctype == "end":
                    await session.send_text_frame(audio_end())
                    continue
                if ctype == "interrupt":
                    from app.telemetry import inc
                    inc("barge_in_count")
                    if active_tool_task and not active_tool_task.done():
                        active_tool_task.cancel()
                    await session.send_text_frame(json.dumps({"clientContent": {"turnComplete": False}}))
                    await browser.send_json({"type": "state", "state": "LISTENING"})
                    continue
                if ctype == "stop":
                    if active_tool_task and not active_tool_task.done():
                        active_tool_task.cancel()
                    await browser.send_json({"type": "state", "state": "COMPLETED"})
                    return

    async def from_gemini() -> None:
        nonlocal lang, locked_lang, active_member_id, greeted, active_tool_task
        async for ev in session.messages():
            if ev.audio:
                await browser.send_bytes(ev.audio)
                continue
            data = dict(ev.json or {})
            kind = data.get("type")
            heard = data.get("text") or ""
            if mode == "talk" and kind == "ready":
                await browser.send_json(data)
                if not greeted:
                    greeted = True
                    from app.services.patient_profile import PatientProfileService
                    greet_text = onboarding_info.get("prompt") or PatientProfileService().greeting_for(lang)
                    await browser.send_json({
                        "type": "greeting",
                        "text": greet_text,
                        "greeting": onboarding_info.get("greeting") or PatientProfileService().greeting_for(lang),
                        "onboarding": onboarding_info,
                        "language": lang,
                    })
                    await session.send_text_frame(proactive_greeting_turn(lang, onboarding_info.get("prompt")))
                continue
            if kind == "final" and data.get("lang"):
                lang_detected = str(data["lang"])
                if lang_detected:
                    if locked_lang is None and lang in ("auto", "app", ""):
                        lang = lang_detected
                        locked_lang = lang_detected
            if mode == "talk" and kind == "final":
                flag = emergency_for(heard, lang)
                if flag:
                    session.blocked = True
                    await browser.send_json({"type": "state", "state": "EMERGENCY_ESCALATION"})
                    await browser.send_json(data)
                    await browser.send_json(flag)
                    await session.send_text_frame(emergency_turn(flag["text"]))
                    continue
            if mode == "talk" and kind == "input_transcript" and session.blocked:
                continue
            if kind == "tool_call":
                name = data.get("name") or "answer_health_question"
                if session.blocked:
                    await session.send_text_frame(tool_response(
                        data.get("id") or "", name, "An emergency was already shown. Say only those instructions."))
                    continue
                question = data.get("question") or ""
                flag = emergency_for(question, lang)
                if flag:
                    session.blocked = True
                    await browser.send_json({"type": "state", "state": "EMERGENCY_ESCALATION"})
                    await browser.send_json(flag)
                    await session.send_text_frame(tool_response(data.get("id") or "", name, flag["text"]))
                    continue
                await browser.send_json({"type": "state", "state": "PROCESSING"})
                active_tool_task = asyncio.create_task(checked_answer_detail(
                    token, profile_id, question, lang,
                    session_id=session_id, history=live_history,
                    locked_lang=locked_lang,
                    active_member_id=active_member_id,
                ))
                try:
                    detail = await active_tool_task
                except asyncio.CancelledError:
                    await session.send_text_frame(tool_response(data.get("id") or "", name, ""))
                    continue
                finally:
                    active_tool_task = None
                reply = detail["reply"]
                spoken = detail["spoken_response"]
                if detail.get("language"):
                    lang = detail["language"]
                    locked_lang = detail["language"]
                if detail.get("active_member_id"):
                    active_member_id = detail["active_member_id"]
                live_history.append({"role": "user", "content": question})
                live_history.append({"role": "assistant", "content": reply})
                if len(live_history) > 16:
                    live_history[:] = live_history[-16:]
                await browser.send_json({"type": "state", "state": "SPEAKING"})
                await browser.send_json({
                    "type": "answer",
                    "text": reply,
                    "spoken_response": spoken,
                    "question": question,
                    "language": lang,
                    "language_meta": detail.get("language_meta"),
                    "structured": detail.get("structured"),
                    "family_context": detail.get("family_context"),
                    "patient_profile": detail.get("patient_profile"),
                    "assessment_panel": detail.get("assessment_panel"),
                    "compact_summary": detail.get("compact_summary"),
                })
                await session.send_text_frame(tool_response(data.get("id") or "", name, spoken))
                continue
            if data:
                await browser.send_json(data)

    browser_task = asyncio.create_task(from_browser())
    gemini_task = asyncio.create_task(from_gemini())
    try:
        done, pending = await asyncio.wait({browser_task, gemini_task}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc:
                log.info("live session ended: %s", type(exc).__name__)
    finally:
        closer = getattr(session.ws, "close", None)
        if closer:
            try:
                await closer()
            except Exception:
                pass


async def connect_live(
    mode: str,
    model: str,
    *,
    lang: str,
    target_lang: str,
    silence_ms: int = 3500,
    patient_context: str = "",
) -> LiveSession:
    import websockets

    key = get_settings().gemini_api_key
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set")
    ws = await websockets.connect(
        LIVE_URL,
        additional_headers={"x-goog-api-key": key},
        max_size=8_000_000,
        open_timeout=20,
    )
    session = LiveSession(ws=ws, mode=mode, lang=lang)
    await session.send_setup(
        model,
        target_lang=target_lang,
        silence_ms=silence_ms,
        patient_context=patient_context,
    )
    return session
