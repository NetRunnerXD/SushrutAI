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


def setup_message(mode: str, model: str, *, lang: str = "en", target_lang: str = "en") -> dict:
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
        body["setup"]["generationConfig"] = {"responseModalities": ["AUDIO"]}
        body["setup"]["systemInstruction"] = {"parts": [{"text": TALK_INSTRUCTION}]}
        body["setup"]["tools"] = [_ANSWER_TOOL]
        body["setup"]["inputAudioTranscription"] = {}
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

    async def send_setup(self, model: str, *, target_lang: str = "en") -> None:
        await self.ws.send(json.dumps(setup_message(self.mode, model, lang=self.lang, target_lang=target_lang)))

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


async def checked_answer(token: str, profile_id: str | None, question: str, lang: str) -> str:
    """The Talk live tool. The existing answer path checks safety and sources."""
    from app.db import SessionLocal
    from app.llm.orchestrator import answer
    from app.security.auth import authenticate_token

    question = (question or "").strip()
    if not question:
        return "I did not catch a question."
    use_lang = "en" if lang in ("auto", "app", "") else lang
    try:
        async with SessionLocal() as db:
            cu = await authenticate_token(token, db, x_profile_id=profile_id)
            result = await answer(db, cu, question[:4000], lang=use_lang, allow_cloud=False, model_pref="balanced")
            return (result.reply or "")[:1500] or "I could not check that just now."
    except Exception:
        log.exception("live answer tool failed")
        return "I could not check that just now. If this feels urgent, call 108 or 112."


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
    model = resolve_model(mode, start.get("model"))
    try:
        session = await connect_live(mode, model, lang=lang, target_lang=target)
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
                if cmd.get("type") == "end":
                    await session.send_text_frame(audio_end())
                    continue
                if cmd.get("type") == "stop":
                    return

    async def from_gemini() -> None:
        async for ev in session.messages():
            if ev.audio:
                await browser.send_bytes(ev.audio)
                continue
            data = dict(ev.json or {})
            kind = data.get("type")
            heard = data.get("text") or ""
            if mode == "talk" and kind == "final":
                flag = emergency_for(heard, lang)
                if flag:
                    session.blocked = True
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
                    await browser.send_json(flag)
                    await session.send_text_frame(tool_response(data.get("id") or "", name, flag["text"]))
                    continue
                reply = await checked_answer(token, profile_id, question, lang)
                await browser.send_json({"type": "answer", "text": reply})
                await session.send_text_frame(tool_response(data.get("id") or "", name, reply))
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


async def connect_live(mode: str, model: str, *, lang: str, target_lang: str) -> LiveSession:
    import websockets

    key = get_settings().gemini_api_key
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set")
    ws = await websockets.connect(f"{LIVE_URL}?key={key}", max_size=8_000_000, open_timeout=20)
    session = LiveSession(ws=ws, mode=mode, lang=lang)
    await session.send_setup(model, target_lang=target_lang)
    return session
