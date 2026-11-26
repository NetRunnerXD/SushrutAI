"""Gemini Live API gateway.

The browser streams 16 kHz PCM to our WebSocket. This module holds the socket to Google, so the API key
never reaches the page. Talk live may call the existing health answer; it does not answer from its own
medical knowledge.
"""
from __future__ import annotations

import base64
import json
import logging
import time
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
_LANG_NAMES = {
    "en": "English", "hi": "Hindi", "bn": "Bengali", "ta": "Tamil", "te": "Telugu", "mr": "Marathi",
    "gu": "Gujarati", "kn": "Kannada", "ml": "Malayalam", "or": "Odia", "pa": "Punjabi", "as": "Assamese", "ur": "Urdu",
}

TALK_INSTRUCTION = (
    "You are the speaking voice of SushrutAI, a careful, warm health-information companion for people in India. "
    "Always call answer_health_question for greetings (like 'hello', 'hi', 'namaste', 'হ্যালো', 'নমস্কার', 'नमस्ते') "
    "as well as every health, symptom, family-member, lab-report, diet, nutrition, "
    "general-wellness, or follow-up reply (including name, age, gender, duration, or yes/no answers), and then speak "
    "only the text that tool returns. Keep the consultation flowing naturally like a real doctor — gently invite "
    "the patient's next thought or offer a helpful next step unless they say goodbye or it is an emergency. "
    "Do not invent a diagnosis, a medicine, or a dose. "
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


def setup_message(
    mode: str,
    model: str,
    *,
    lang: str = "en",
    target_lang: str = "en",
    sos_enabled: bool = True,
    context_summary: str = "",
    speech_mode: str = "general",
    response_style: str = "direct",
    voice_name: str | None = None,
) -> dict:
    """The first frame sent to Gemini. mode is transcribe, translate, or talk."""
    body: dict[str, Any] = {"setup": {"model": f"models/{model}"}}
    specific = lang not in ("auto", "app", "")
    codes = [_BCP.get(lang, lang)] if specific else []
    if mode == "transcribe":
        body["setup"]["generationConfig"] = {"responseModalities": ["TEXT"]}
        body["setup"]["inputAudioTranscription"] = {"languageCodes": codes}
    elif mode == "translate":
        target = "en" if target_lang in ("auto", "app", "") else target_lang.split("-")[0]
        body["setup"]["generationConfig"] = {
            "responseModalities": ["AUDIO"],
            "translationConfig": {"targetLanguageCode": target, "echoTargetLanguage": False},
        }
        body["setup"]["inputAudioTranscription"] = {"languageCodes": codes} if codes else {}
        body["setup"]["outputAudioTranscription"] = {}
    else:
        inst = TALK_INSTRUCTION
        if not sos_enabled:
            inst = inst.replace(
                "If that text says to call 108 or 112, say that and stop.",
                "Continue the consultation naturally and answer follow-up questions without stopping.",
            )
        if specific:
            lname = _LANG_NAMES.get(lang, lang)
            inst += (
                f" The user's selected language is {lname} ({lang}). "
                f"Always listen, transcribe, and speak ONLY in {lname}; do not translate or switch to another language."
            )
        if speech_mode == "elderly":
            inst += (
                "\n\nELDERLY & PATIENT-FRIENDLY MODE: Speak slowly, warmly, and clearly in simple everyday words. "
                "Never interrupt the patient during thinking pauses (wait patiently up to 25 seconds). "
                "Accommodate fragmented or unstructured speech, topic switches, repetitions, and corrections without rushing. "
                "Handle interruptions like an attentive face-to-face doctor: "
                "(1) If the patient corrects you (e.g. 'না না, এক মাস ধরে'), immediately drop the old assumption, acknowledge naturally (e.g. 'ওহ, বুঝেছি'), and do NOT restart your previous unfinished answer; "
                "(2) If they add another symptom mid-sentence, weave it into the clinical picture and reassess; "
                "(3) Short acknowledgements ('হ্যাঁ', 'আচ্ছা', 'ঠিক আছে', 'hmm', 'okay') while you explain do not need repetition; "
                "(4) If they say 'wait' or pause to remember, stay silent and let them recall; "
                "(5) Never silently overwrite contradictory medical details (e.g. 'no insulin' vs 'morning insulin') — gently ask to clarify. "
                "If the patient speaks for a long time and diverges from their main symptom, gently acknowledge what they shared and ask ONE short, focused clarifying question before assessing. "
                "Ask one short, easy-to-understand question at a time and gently confirm key details."
            )
        if response_style == "direct":
            inst += (
                "\n\nNATURAL DIRECT DOCTOR-PATIENT STYLE: Speak like a warm, caring family doctor talking directly to the patient. "
                "Skip long introductory stories; give the direct takeaway naturally in 2–4 crisp sentences plus 1 focused follow-up question."
            )
        if context_summary.strip():
            inst += f"\n\nOngoing conversation context (continue naturally from this):\n{context_summary.strip()}"
        gen_cfg: dict[str, Any] = {"responseModalities": ["AUDIO"]}
        if voice_name and voice_name.strip():
            gen_cfg["speechConfig"] = {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice_name.strip()}}
            }
        body["setup"]["generationConfig"] = gen_cfg
        body["setup"]["systemInstruction"] = {"parts": [{"text": inst}]}
        body["setup"]["tools"] = [_ANSWER_TOOL]
        body["setup"]["inputAudioTranscription"] = {"languageCodes": codes} if codes else {}
        body["setup"]["outputAudioTranscription"] = {}
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


def doctor_interrupt_turn(preserved_text: str) -> str:
    """Gently prompt the live model to act like an attentive doctor guiding a long/divergent monologue."""
    snippet = (preserved_text or "").strip()[-400:]
    return json.dumps({"clientContent": {"turns": [{"role": "user", "parts": [{
        "text": (
            "The patient has been sharing a lot of details: "
            f"\"{snippet}\". "
            "Gently and warmly step in like a caring doctor: briefly acknowledge what you heard so far, "
            "and ask ONE short, focused clarifying question about their main symptom before continuing."
        ),
    }]}], "turnComplete": True}})


def is_self_echo(heard: str, recent_ai_text: str) -> bool:
    """True when ASR output closely matches what the AI just spoke over the speaker."""
    import re
    norm = lambda s: re.sub(r"[^\w\s]", "", (s or "").lower()).strip()
    h = norm(heard)
    a = norm(recent_ai_text)
    if not h or not a:
        return False
    h_words = [w for w in h.split() if len(w) > 1]
    if len(h_words) < 3:
        return False
    if h in a:
        return True
    a_words = set(w for w in a.split() if len(w) > 1)
    if not a_words:
        return False
    overlap = sum(1 for w in h_words if w in a_words) / len(h_words)
    return overlap >= 0.65


def barge_in_recovery_turn(interruption_type: str, spoken_prefix: str, new_speech: str) -> str:
    """Guide Gemini Live to recover naturally after a patient barge-in without restarting the old answer."""
    prefix = (spoken_prefix or "").strip()[-240:]
    heard = (new_speech or "").strip()[:400]
    if interruption_type == "CORRECTION":
        guidance = (
            f"The patient interrupted your response (you had said: \"{prefix}\") to correct you: \"{heard}\". "
            "Do NOT restart your previous answer. Warmly acknowledge the correction in natural everyday words and continue from the corrected information."
        )
    elif interruption_type == "ADDED_SYMPTOM":
        guidance = (
            f"While you were speaking (\"{prefix}\"), the patient remembered and added another symptom: \"{heard}\". "
            "Incorporate this new symptom alongside their earlier symptoms, reassess urgency, and ask one focused follow-up question."
        )
    elif interruption_type == "THINKING_PAUSE":
        guidance = (
            f"The patient asked for a moment to think/remember: \"{heard}\". "
            "Respond only with a brief, warm reassurance (e.g., 'Take your time, no rush') and wait silently."
        )
    else:
        guidance = (
            f"The patient interrupted while you were saying \"{prefix}\" with: \"{heard}\". "
            "Do NOT repeat your interrupted sentence; address what they just said naturally."
        )
    return json.dumps({"clientContent": {"turns": [{"role": "user", "parts": [{"text": guidance}]}], "turnComplete": True}})


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
    if sc.get("turnComplete") or sc.get("turn_complete"):
        out.append(LiveEvent(json={"type": "turn_complete"}))
    tc = msg.get("toolCall") or msg.get("tool_call") or {}
    for call in tc.get("functionCalls") or tc.get("function_calls") or []:
        args = call.get("args") or {}
        out.append(LiveEvent(json={
            "type": "tool_call",
            "id": call.get("id") or "",
            "name": call.get("name") or "",
            "question": (args.get("question") if isinstance(args, dict) else "") or "",
        }))
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
        sos_enabled: bool = True,
        context_summary: str = "",
        speech_mode: str = "general",
        response_style: str = "direct",
        voice_name: str | None = None,
    ) -> None:
        await self.ws.send(json.dumps(setup_message(
            self.mode, model, lang=self.lang, target_lang=target_lang,
            sos_enabled=sos_enabled, context_summary=context_summary,
            speech_mode=speech_mode, response_style=response_style,
            voice_name=voice_name,
        )))

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


async def checked_answer_full(
    token: str,
    profile_id: str | None,
    question: str,
    lang: str,
    *,
    session_id: str | None = None,
    account_id: str | None = None,
    history: list[dict[str, Any]] | None = None,
    replace_history: bool = False,
    use_record: bool = False,
    speech_mode: str | None = None,
    response_style: str | None = None,
) -> dict[str, Any]:
    """The Talk live tool. Returns the full structured TurnResult payload (for UI) and spoken reply."""
    from sqlalchemy import delete, select

    from app.db import SessionLocal
    from app.llm.orchestrator import answer
    from app.llm.providers import full_cloud_allowed
    from app.models import ChatSession, Message
    from app.security.auth import authenticate_token, resolve_current_user
    from app.security.settings import cloud_allowed, get_settings_row

    question = (question or "").strip()
    if not question:
        return {"reply": "I did not catch a question."}
    use_lang = "en" if lang in ("auto", "app", "") else lang
    try:
        from app.llm import intent as intent_mod
        from app.llm.orchestrator import _fixed_turn
        from app.safety import red_flags

        conv = intent_mod._phrase_intent(question)
        if conv in ("thanks_bye", "about_app"):
            fast_res = _fixed_turn(conv, use_lang, red_flags.RedFlagResult(False))
            return {
                "reply": fast_res.reply,
                "structured": fast_res.structured,
                "citations": fast_res.citations,
                "disclaimer": fast_res.disclaimer,
                "red_flag": fast_res.red_flag,
                "provider": fast_res.provider,
                "model": fast_res.model,
                "tier": fast_res.tier,
                "speed": "instant",
                "latency_ms": 1,
                "kind": fast_res.kind or conv,
                "clinical": None,
            }
    except Exception:
        pass
    try:
        async with SessionLocal() as db:
            cu = (
                await resolve_current_user(account_id, db, x_profile_id=profile_id)
                if account_id
                else await authenticate_token(token, db, x_profile_id=profile_id)
            )
            allow_full = await cloud_allowed(db, cu.account_id, "cloud_llm_full")
            allow_deid = await cloud_allowed(db, cu.account_id, "cloud_llm_deidentified")
            s_row = await get_settings_row(db, cu.account_id)
            sos_enabled = bool(getattr(s_row, "sos_enabled", True))
            eff_speech_mode = speech_mode or getattr(s_row, "speech_mode", None) or "general"
            eff_response_style = response_style or getattr(s_row, "response_style", None) or "direct"
            pref = getattr(s_row, "model_pref", None) or "auto"
            if pref == "auto":
                pref = "gemini_fast" if allow_full else "fast"
            if pref in ("gemini", "gemini_fast") and not allow_full:
                pref = "fast"
            log.info("Talk Live checked_answer_full using model_pref=%s (allow_full=%s, sos=%s, use_record=%s)", pref, allow_full, sos_enabled, use_record)
            full_cloud_allowed.set(allow_full)
            sid = session_id
            if sid and await db.get(ChatSession, sid) is None:
                db.add(ChatSession(id=sid, user_id=cu.id, lang=use_lang, channel="voice"))
                await db.commit()

            effective_history: list[dict[str, Any]] | None = None
            if sid:
                prior_rows = list(await db.scalars(select(Message).where(Message.session_id == sid).order_by(Message.created_at)))
                stored = [
                    {
                        "role": m.role,
                        "content": cu.cipher.dec(m.content_enc),
                        **({"structured": m.structured} if m.role == "assistant" and m.structured else {}),
                    }
                    for m in prior_rows
                ]
                if replace_history and history is not None:
                    keep = len(history)
                    drop = [m.id for m in prior_rows[keep:]]
                    if drop:
                        await db.execute(delete(Message).where(Message.id.in_(drop)))
                        await db.commit()
                    effective_history = stored[:keep] if len(stored) >= keep else [
                        {"role": str(h.get("role") or "user"), "content": str(h.get("content") or "")}
                        for h in history
                    ]
                elif stored:
                    effective_history = stored
                elif history:
                    effective_history = [
                        {"role": str(h.get("role") or "user"), "content": str(h.get("content") or "")}
                        for h in history
                    ]
            elif history:
                effective_history = [
                    {"role": str(h.get("role") or "user"), "content": str(h.get("content") or "")}
                    for h in history
                ]

            result = await answer(
                db, cu, question[:4000], lang=use_lang, session_id=sid,
                history=effective_history,
                allow_cloud=allow_deid, model_pref=pref, use_record=use_record, sos_enabled=sos_enabled,
                live_mode=True, speech_mode=eff_speech_mode, response_style=eff_response_style,
            )
            reply = result.reply or "I could not check that just now."
            clinical_out: dict[str, Any] = {}
            if result.structured is not None and (result.kind or "") not in (
                "greeting", "thanks_bye", "about_app", "off_topic", "attachment", "medicine_photo", "clarify",
                "general_health", "nutrition", "lab_report",
            ):
                try:
                    from app.clinical.extract import extract_clinical_turn
                    from app.clinical.facts import record_clinical_facts
                    from app.clinical.followup import record_followup_answer
                    from app.clinical.interview import AdaptiveClinicalInterviewEngine
                    from app.clinical.investigations import InvestigationRecommendationEngine
                    from app.clinical.persistence import get_or_create_encounter, persist_extraction
                    from app.clinical.routing import MedicalCategoryClassifier, SpecialistRoutingEngine
                    from app.clinical.subject_context import detect_subject_relation, upsert_subject_relation_fact
                    from app.memory.context import PatientContextBuilder
                    from app.safety.clinical_safety import evaluate_safety

                    nlp_input = f"{question}\n{result.english}" if getattr(result, "english", None) and result.english.strip() != question.strip() else question
                    enc = await get_or_create_encounter(
                        db, cu, chat_session_id=sid or "live-voice", channel="voice", language=use_lang, chief_complaint=question[:240]
                    )
                    extraction = await extract_clinical_turn(
                        nlp_input,
                        patient_id=cu.id,
                        encounter_id=enc.id,
                        language=use_lang,
                        raw_transcript=question,
                        source="voice_transcript",
                        use_llm=False,
                    )
                    persist_summary = (
                        await persist_extraction(db, cu, extraction, encounter_id=enc.id)
                        if use_record
                        else {"discrepancies": []}
                    )
                    await record_clinical_facts(
                        db, cu,
                        encounter_id=enc.id,
                        conversation_id=sid,
                        turn_id=None,
                        transcript_id=None,
                        transcript_text=question,
                        payload=extraction,
                        discrepancy_notes=persist_summary.get("discrepancies"),
                    )
                    await record_followup_answer(db, cu, encounter_id=enc.id, answer_text=question, extraction=extraction)
                    subj = detect_subject_relation(nlp_input)
                    if subj.get("is_third_party") and subj.get("relation"):
                        await upsert_subject_relation_fact(
                            db, cu,
                            encounter_id=enc.id,
                            relation=str(subj["relation"]),
                            is_family=bool(subj.get("is_family")),
                            age=subj.get("age"),
                            gender=subj.get("gender"),
                            symptoms=[s.name for s in extraction.symptoms],
                        )
                        await db.commit()
                    elif extraction.symptoms:
                        await upsert_subject_relation_fact(
                            db, cu,
                            encounter_id=enc.id,
                            relation="self",
                            is_family=True,
                            symptoms=[s.name for s in extraction.symptoms],
                        )
                        await db.commit()
                    mem_ctx = await PatientContextBuilder().build(
                        db, cu, query_text=nlp_input, current_symptoms=[s.name for s in extraction.symptoms],
                        encounter_id=enc.id, encounter_only=not use_record,
                    )
                    safety = evaluate_safety(nlp_input, lang=use_lang, extraction=extraction)
                    clf = MedicalCategoryClassifier().classify(
                        user_text=nlp_input, extraction=extraction, mem=mem_ctx, safety=safety,
                    )
                    routing = SpecialistRoutingEngine().route(
                        classification=clf, user_text=nlp_input, safety=safety, mem=mem_ctx,
                    )
                    investigations = await InvestigationRecommendationEngine().recommend(
                        db, cu, encounter_id=enc.id, mem=mem_ctx, safety=safety, user_text=nlp_input,
                    )
                    interview_res = await AdaptiveClinicalInterviewEngine().evaluate_and_next_question(
                        db, cu,
                        encounter_id=enc.id,
                        user_text=question,
                        extraction=extraction,
                        mem=mem_ctx,
                        safety=safety,
                        lang=use_lang,
                        is_symptom_turn=(result.kind == "symptoms"),
                        allow_emergency_interrupt=sos_enabled,
                    )
                    fq = interview_res.get("question")
                    not_rf = not (isinstance(result.red_flag, dict) and result.red_flag.get("triggered"))
                    if fq and (fq.get("question") or fq.get("question_text")) and not_rf:
                        q_txt = str(fq.get("question") or fq.get("question_text") or "").strip()
                        if q_txt and q_txt not in reply:
                            reply = f"{reply}\n\n{q_txt}"
                    elif not fq and not_rf and not reply.rstrip().endswith("?"):
                        cont_q = str(interview_res.get("continuation_question") or "").strip()
                        if not cont_q and isinstance(result.structured, dict):
                            fqs = result.structured.get("follow_up_questions") or []
                            if fqs and isinstance(fqs[0], str):
                                cont_q = fqs[0].strip()
                        if cont_q and cont_q not in reply:
                            reply = f"{reply}\n\n{cont_q}"
                    clinical_out = {
                        "specialty_classification": {k: v for k, v in clf.items() if not k.startswith("_")},
                        "specialist_routing": routing,
                        "investigations": investigations,
                        "followup_question": fq,
                    }
                except Exception:  # noqa: BLE001 — never fail live voice reply
                    log.exception("live clinical routing/investigations failed")
            return {
                "reply": reply,
                "structured": result.structured,
                "citations": result.citations,
                "disclaimer": result.disclaimer,
                "red_flag": result.red_flag,
                "provider": result.provider,
                "model": result.model,
                "tier": result.tier,
                "speed": result.speed,
                "latency_ms": result.latency_ms,
                "kind": result.kind,
                "clinical": clinical_out or None,
            }
    except Exception:
        log.exception("live answer tool failed")
        return {"reply": "I could not check that just now. If this feels urgent, call 108 or 112."}


async def checked_answer(
    token: str,
    profile_id: str | None,
    question: str,
    lang: str,
    *,
    session_id: str | None = None,
) -> str:
    """Backward-compatible string wrapper around checked_answer_full."""
    out = await checked_answer_full(token, profile_id, question, lang, session_id=session_id)
    return str(out.get("reply") or "")[:1500] or "I could not check that just now."


async def serve(browser: Any, *, token: str, profile_id: str | None) -> None:
    """Browser WebSocket ↔ Gemini Live. Closes with 4401 or 4403 before accept when not allowed."""
    import asyncio

    from fastapi import HTTPException

    from app.db import SessionLocal
    from app.security.auth import authenticate_token
    from app.security.consent import active_purposes
    from app.security.settings import cloud_allowed, get_settings_row

    if not token:
        await browser.close(code=4401)
        return
    sos_enabled = True
    async with SessionLocal() as db:
        try:
            cu = await authenticate_token(token, db, x_profile_id=profile_id)
        except HTTPException:
            await browser.close(code=4401)
            return
        allowed = ("core_service" in await active_purposes(db, cu.account_id)
                   and await cloud_allowed(db, cu.account_id, "cloud_llm_live"))
        s_row = await get_settings_row(db, cu.account_id)
        sos_enabled = bool(getattr(s_row, "sos_enabled", True))
        default_speech_mode = getattr(s_row, "speech_mode", None) or "general"
        default_response_style = getattr(s_row, "response_style", None) or "direct"
        authed_account_id = cu.account_id
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
    import uuid as _uuid

    lang = str(start.get("lang") or "en")
    target = str(start.get("target_lang") or lang)
    model = resolve_model(mode, start.get("model"))
    init_history = start.get("history") if isinstance(start.get("history"), list) else []
    live_state: dict[str, Any] = {
        "session_id": str(start.get("session_id") or "").strip() or str(_uuid.uuid4()),
        "use_record": bool(start.get("use_record", False)),
        "history": init_history,
        "replace_history": bool(start.get("replace_history", False)),
        "speech_mode": str(start.get("speech_mode") or default_speech_mode or "general"),
        "response_style": str(start.get("response_style") or default_response_style or "direct"),
    }
    context_summary = "\n".join(
        f"{'Patient' if str(h.get('role')) == 'user' else 'Assistant'}: {str(h.get('content') or '')[:240]}"
        for h in init_history[-6:]
        if str(h.get("content") or "").strip()
    )
    try:
        session = await connect_live(
            mode, model, lang=lang, target_lang=target,
            sos_enabled=sos_enabled, context_summary=context_summary,
            speech_mode=live_state["speech_mode"],
            response_style=live_state["response_style"],
            voice_name=(str(start.get("voice_name") or "").strip() or None),
        )
    except Exception as e:
        log.warning("gemini live connect failed: %s", e)
        await browser.send_json({"type": "error", "message": "Gemini Live did not connect. Try again in a minute."})
        await browser.close()
        return

    async def from_browser() -> None:
        while True:
            msg = await browser.receive()
            if msg.get("type") == "websocket.disconnect":
                return
            if msg.get("bytes"):
                await session.send_audio(msg["bytes"])
            elif msg.get("text"):
                try:
                    cmd = json.loads(msg["text"])
                except json.JSONDecodeError:
                    continue
                if cmd.get("type") == "sync_context":
                    if cmd.get("session_id"):
                        live_state["session_id"] = str(cmd["session_id"])
                    if "use_record" in cmd:
                        live_state["use_record"] = bool(cmd["use_record"])
                    if cmd.get("speech_mode") in ("general", "elderly"):
                        live_state["speech_mode"] = str(cmd["speech_mode"])
                    if cmd.get("response_style") in ("direct", "detailed"):
                        live_state["response_style"] = str(cmd["response_style"])
                    if isinstance(cmd.get("history"), list):
                        live_state["history"] = cmd["history"]
                        live_state["replace_history"] = bool(cmd.get("replace_history", True))
                    continue
                if cmd.get("type") == "barge_in":
                    from app.conversation.state import InterruptionType, classify_interruption
                    barge_text = str(cmd.get("text") or "").strip()
                    spoken_prefix = str(cmd.get("spoken_prefix") or active_gen.get("spoken_text") or "").strip()
                    itype = classify_interruption(
                        barge_text,
                        ai_is_asking_question=bool(active_gen.get("ai_asking_question")),
                        lang=lang,
                    )
                    if itype == InterruptionType.EMERGENCY:
                        prev_t = active_tool.get("task")
                        if prev_t is not None and not prev_t.done():
                            prev_t.cancel()
                        flag = emergency_for(barge_text, lang) if sos_enabled else None
                        if flag:
                            session.blocked = True
                            await browser.send_json(flag)
                            await session.send_text_frame(emergency_turn(flag["text"]))
                        continue
                    if itype == InterruptionType.ACKNOWLEDGEMENT and not active_gen.get("ai_asking_question"):
                        await browser.send_json({
                            "type": "barge_in_ack",
                            "interruption_type": itype.value,
                            "should_stop_tts": False,
                        })
                        continue
                    prev_t = active_tool.get("task")
                    if prev_t is not None and not prev_t.done():
                        prev_t.cancel()
                    active_gen["id"] += 1
                    active_gen["spoken_text"] = ""
                    await browser.send_json({
                        "type": "barge_in_ack",
                        "interruption_type": itype.value,
                        "should_stop_tts": True,
                        "spoken_prefix": spoken_prefix[:400],
                    })
                    if barge_text:
                        await session.send_text_frame(barge_in_recovery_turn(itype.value, spoken_prefix, barge_text))
                    continue
                if cmd.get("type") == "end":
                    await session.send_text_frame(audio_end())
                    continue
                if cmd.get("type") == "stop":
                    return

    tool_tasks: set[asyncio.Task] = set()
    active_tool: dict[str, Any] = {"task": None, "question": ""}
    active_gen: dict[str, Any] = {
        "id": 0, "spoken_text": "", "ai_asking_question": False,
        "recent_ai_text": "", "last_ai_at": 0.0,
    }

    async def _run_tool(call_id: str, name: str, question: str) -> None:
        try:
            await browser.send_json({"type": "processing", "question": question})
            replace_flag = bool(live_state.get("replace_history", False))
            live_state["replace_history"] = False
            payload = await checked_answer_full(
                token,
                profile_id,
                question,
                lang,
                session_id=live_state["session_id"],
                account_id=authed_account_id,
                history=live_state.get("history"),
                replace_history=replace_flag,
                use_record=bool(live_state.get("use_record", False)),
                speech_mode=str(live_state.get("speech_mode") or "general"),
                response_style=str(live_state.get("response_style") or "direct"),
            )
            reply = str(payload.get("reply") or "")
            if reply:
                active_gen["recent_ai_text"] = f"{active_gen.get('recent_ai_text', '')} {reply}".strip()[-1500:]
                active_gen["last_ai_at"] = time.monotonic()
            if isinstance(payload.get("red_flag"), dict) and payload["red_flag"].get("triggered"):
                rf = payload["red_flag"]
                await browser.send_json({"type": "red_flag", "text": "\n\n".join(rf.get("messages") or []) or reply, "safety": rf})
            await browser.send_json({"type": "answer", "question": question, "text": reply, **payload})
            await session.send_text_frame(tool_response(call_id, name, reply[:1500]))
        except asyncio.CancelledError:
            log.info("cancelled in-flight live tool to merge additional speech")
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("live tool execution failed: %s", e)
            try:
                await browser.send_json({"type": "error", "message": "Could not process that question. Please try again."})
            except Exception:
                pass
        finally:
            if active_tool.get("task") is asyncio.current_task():
                active_tool["task"] = None
                active_tool["question"] = ""

    async def from_gemini() -> None:
        monologue: dict[str, Any] = {"started_at": 0.0, "text": "", "interrupted": False}
        async for ev in session.messages():
            if ev.audio:
                await browser.send_bytes(ev.audio)
                continue
            data = dict(ev.json or {})
            kind = data.get("type")
            heard = data.get("text") or ""
            if mode == "talk" and kind in ("interim", "final", "input_transcript") and heard.strip():
                if (time.monotonic() - float(active_gen.get("last_ai_at") or 0.0) <= 2.5) and is_self_echo(heard, str(active_gen.get("recent_ai_text") or "")):
                    log.debug("dropped self-echo transcript: %s", heard[:80])
                    continue
                if not monologue["started_at"]:
                    monologue["started_at"] = time.monotonic()
                if heard.strip() not in monologue["text"]:
                    monologue["text"] = f"{monologue['text']} {heard.strip()}".strip()
                if (
                    live_state.get("speech_mode") == "elderly"
                    and not monologue["interrupted"]
                    and not session.blocked
                    and (
                        len(monologue["text"].split()) >= 55
                        or (time.monotonic() - monologue["started_at"] >= 28.0)
                    )
                ):
                    monologue["interrupted"] = True
                    await browser.send_json({
                        "type": "doctor_interrupt",
                        "reason": "long_monologue",
                        "preserved_text": monologue["text"],
                    })
                    await session.send_text_frame(doctor_interrupt_turn(monologue["text"]))
            if sos_enabled and mode == "talk" and kind == "final":
                flag = emergency_for(heard, lang)
                if flag:
                    session.blocked = True
                    await browser.send_json(data)
                    await browser.send_json(flag)
                    await session.send_text_frame(emergency_turn(flag["text"]))
                    continue
            if mode == "talk" and kind == "input_transcript" and session.blocked:
                continue
            if kind == "output_transcript" and heard.strip():
                active_gen["spoken_text"] = f"{active_gen['spoken_text']} {heard.strip()}".strip()[-1200:]
                active_gen["recent_ai_text"] = f"{active_gen.get('recent_ai_text', '')} {heard.strip()}".strip()[-1500:]
                active_gen["last_ai_at"] = time.monotonic()
                active_gen["ai_asking_question"] = active_gen["spoken_text"].rstrip().endswith("?")
            if kind == "turn_complete":
                monologue["started_at"] = 0.0
                monologue["text"] = ""
                monologue["interrupted"] = False
                active_gen["spoken_text"] = ""
                active_gen["ai_asking_question"] = False
            if kind == "tool_call":
                monologue["started_at"] = 0.0
                monologue["text"] = ""
                monologue["interrupted"] = False
                active_gen["spoken_text"] = ""
                active_gen["ai_asking_question"] = False
                name = data.get("name") or "answer_health_question"
                if session.blocked:
                    await session.send_text_frame(tool_response(
                        data.get("id") or "", name, "An emergency was already shown. Say only those instructions."))
                    continue
                question = data.get("question") or ""
                if (time.monotonic() - float(active_gen.get("last_ai_at") or 0.0) <= 2.5) and is_self_echo(question, str(active_gen.get("recent_ai_text") or "")):
                    log.info("ignored self-echo tool_call: %s", question[:80])
                    await session.send_text_frame(tool_response(data.get("id") or "", name, "Echo ignored; wait silently for the patient."))
                    continue
                flag = emergency_for(question, lang) if sos_enabled else None
                if flag:
                    session.blocked = True
                    await browser.send_json(flag)
                    await session.send_text_frame(tool_response(data.get("id") or "", name, flag["text"]))
                    continue
                prev_task = active_tool.get("task")
                if prev_task is not None and not prev_task.done():
                    prev_task.cancel()
                    prev_q = str(active_tool.get("question") or "").strip()
                    if prev_q and prev_q.lower() not in question.lower():
                        question = f"{prev_q} {question}".strip()
                active_tool["question"] = question
                t = asyncio.create_task(_run_tool(data.get("id") or "", name, question))
                active_tool["task"] = t
                tool_tasks.add(t)
                t.add_done_callback(tool_tasks.discard)
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
                if task is gemini_task:
                    try:
                        await browser.send_json({"type": "error", "message": f"Gemini Live disconnected ({type(exc).__name__})."})
                    except Exception:
                        pass
    finally:
        for t in list(tool_tasks):
            t.cancel()
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
    sos_enabled: bool = True,
    context_summary: str = "",
    speech_mode: str = "general",
    response_style: str = "direct",
    voice_name: str | None = None,
) -> LiveSession:
    import websockets

    key = get_settings().gemini_api_key
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set")
    ws = await websockets.connect(f"{LIVE_URL}?key={key}", max_size=8_000_000, open_timeout=20)
    session = LiveSession(ws=ws, mode=mode, lang=lang)
    await session.send_setup(
        model, target_lang=target_lang, sos_enabled=sos_enabled,
        context_summary=context_summary, speech_mode=speech_mode,
        response_style=response_style, voice_name=voice_name,
    )
    return session
