"""Speech-to-text and text-to-speech — local first.

STT: IndicConformer-600M (local) | faster-whisper (local) → the cloud voice the person chose
     (Sarvam or ElevenLabs, `cloud_voice`) → the other of those two → Sarvam → Bhashini → Groq Whisper
     (`cloud_speech`) → 501 (browser Web Speech API).
TTS: the chosen cloud voice → the other cloud voice → local Indic TTS → Sarvam → Bhashini → 501.
"""
from __future__ import annotations

from typing import Literal

import asyncio
import base64
import logging
import time

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import SessionLocal, get_db
from app.security.auth import CurrentUser, authenticate_token, current_user
from app.security.consent import active_purposes
from app.security.settings import cloud_allowed
from app.langs import INDIC as LANG_INDIC
from app.speech import models as M
from app.speech import normalize as N
from app.speech.refine import persist_transcript, refine_transcript
from app.speech.stream import StreamingSpeechSession

router = APIRouter(tags=["voice"])
log = logging.getLogger("sushrut.voice")

BCP47 = {"en": "en-IN", "hi": "hi-IN", "bn": "bn-IN", "ta": "ta-IN", "te": "te-IN", "mr": "mr-IN",
         "gu": "gu-IN", "kn": "kn-IN", "ml": "ml-IN", "or": "od-IN", "pa": "pa-IN", "as": "as-IN", "ur": "ur-IN"}
INDIC = set(LANG_INDIC)
_local_ok = {"conformer": M.available("onnxruntime") and M.available("transformers"),
             "whisper": M.available("faster_whisper"), "tts": M.available("transformers"),
             "sherpa_onnx": M.available("sherpa_onnx")}


def _local_stt(audio_bytes: bytes, lang: str, ui: str = "en") -> dict | None:
    """lang: a language code, or "auto" = detect which of {app language, English, Hindi} is being spoken."""
    t0 = time.time()
    try:
        audio = M.decode_audio(audio_bytes)
    except Exception as e:  # noqa: BLE001
        log.warning("audio decode failed: %s", e)
        return None
    target_lang = ui if lang == "auto" else lang
    if len(audio) < 1600:  # < 0.1 s
        return {
            "text": "", "raw_transcript": "", "normalized_transcript": "",
            "lang": target_lang, "detected_languages": [target_lang],
            "code_switched": False, "banglish_influenced": False,
            "confidence": 0.0, "low_confidence": True,
            "clarification_prompt": N.CLARIFY_PROMPTS.get(target_lang, N.CLARIFY_PROMPTS["en"]),
            "segments": [], "engine": "none (no speech)", "tier": "L", "seconds": 0,
        }
    conf = None
    if lang == "auto":
        lang, conf = ui, 0.0
        if _local_ok["whisper"]:
            try:
                # spoken Hindi and Urdu are nearly the same: an Urdu user's speech stays Urdu (written in Nastaliq)
                allowed = [ui, "en"] if ui == "ur" else [ui, "en", "hi"]
                lang, conf = M.detect_spoken_language(audio, allowed, fallback=ui)
            except Exception as e:  # noqa: BLE001
                log.warning("language detection failed: %s", e)
    res = _transcribe(audio, lang, t0)
    if res is not None:
        raw_segs = res.pop("_segments", [])
        seg_objs = [
            N.SpeechSegment(start=s.get("start", 0.0), end=s.get("end", 0.0), text=s.get("text", ""),
                            confidence=s.get("confidence"), language=s.get("language"))
            for s in raw_segs
        ]
        if not seg_objs:
            spans = N.segment_speech_audio(audio)
            seg_objs = [
                N.SpeechSegment(start=s, end=e, text=res["text"] if len(spans) == 1 else "", language=res["lang"])
                for s, e in spans
            ]
        eff_conf = conf if conf is not None else res.pop("_seg_conf", None)
        analysis = N.analyze_transcript(
            res["text"],
            lang_hint=res["lang"],
            audio_conf=eff_conf if (eff_conf is not None and eff_conf > 0) else None,
            segments=seg_objs,
            audio=audio,
        )
        res["confidence"] = conf if conf is not None else analysis.confidence
        res["raw_transcript"] = analysis.raw_transcript
        res["normalized_transcript"] = analysis.normalized_transcript
        res["detected_languages"] = analysis.detected_languages
        res["code_switched"] = analysis.code_switched
        res["banglish_influenced"] = analysis.banglish_influenced
        res["low_confidence"] = analysis.low_confidence
        res["clarification_prompt"] = analysis.clarification_prompt
        res["segments"] = [s.as_dict() for s in analysis.segments]
        res["audio_confidence"] = analysis.confidence
    return res


def _transcribe(audio, lang: str, t0: float) -> dict | None:
    if lang in INDIC and _local_ok["conformer"]:
        try:
            text = M.stt_indic(audio, lang)
            return {"text": text, "lang": lang, "engine": "IndicConformer-600M (local)",
                    "tier": "L", "seconds": round(time.time() - t0, 2)}
        except Exception as e:  # noqa: BLE001
            log.warning("IndicConformer failed: %s", e)
    if _local_ok["whisper"]:
        try:
            text, detected = M.stt_whisper(audio, lang)
            segs, seg_conf = M.last_whisper_meta() if hasattr(M, "last_whisper_meta") else ([], None)
            return {"text": text, "lang": M.LANG_ALIASES.get(detected, detected) if lang not in INDIC else lang,
                    "engine": "faster-whisper large-v3-turbo (local)", "tier": "L",
                    "seconds": round(time.time() - t0, 2), "_segments": segs, "_seg_conf": seg_conf}
        except Exception as e:  # noqa: BLE001
            log.warning("faster-whisper failed: %s", e)
    return None


async def _sarvam_stt(audio: bytes, mime: str, lang: str) -> dict | None:
    key = get_settings().sarvam_api_key
    if not key:
        return None
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post("https://api.sarvam.ai/speech-to-text", headers={"api-subscription-key": key},
                         files={"file": ("audio.webm", audio, mime)},
                         data={"model": "saarika:v2.5", "language_code": BCP47.get(lang, "unknown")})
    if r.status_code >= 400:
        log.warning("Sarvam STT %s: %s", r.status_code, r.text[:200])
        return None
    j = r.json()
    return {"text": j.get("transcript", ""), "lang": j.get("language_code"), "engine": "Sarvam Saarika", "tier": "IN"}


async def _groq_stt(audio: bytes, mime: str, lang: str) -> dict | None:
    s = get_settings()
    if not s.groq_api_key:
        return None
    data = {"model": s.groq_stt_model, "prompt": M.DRUG_HOTWORDS, "response_format": "json"}
    # Whisper's Odia code is `or`. If Groq rejects a language hint, retry once with detection.
    if lang not in ("auto", "app"):
        data["language"] = lang
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post("https://api.groq.com/openai/v1/audio/transcriptions",
                         headers={"Authorization": f"Bearer {s.groq_api_key}"},
                         files={"file": ("audio.webm", audio, mime)}, data=data)
        if r.status_code == 400 and "language" in data:
            data.pop("language")
            r = await c.post("https://api.groq.com/openai/v1/audio/transcriptions",
                             headers={"Authorization": f"Bearer {s.groq_api_key}"},
                             files={"file": ("audio.webm", audio, mime)}, data=data)
    if r.status_code >= 400:
        log.warning("Groq STT %s: %s", r.status_code, r.text[:200])
        return None
    return {"text": r.json().get("text", ""), "lang": lang, "engine": "Groq Whisper large-v3", "tier": "C"}


# ---------------------------------------------------------------- ElevenLabs (chosen by the person)
async def _eleven_stt(audio: bytes, mime: str, lang: str) -> dict | None:
    s = get_settings()
    if not s.elevenlabs_api_key:
        return None
    data = {"model_id": s.elevenlabs_stt_model}
    if lang not in ("auto", "app"):
        data["language_code"] = lang
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post("https://api.elevenlabs.io/v1/speech-to-text", headers={"xi-api-key": s.elevenlabs_api_key},
                         files={"file": ("audio.webm", audio, mime)}, data=data)
    if r.status_code >= 400:
        log.warning("ElevenLabs STT %s: %s", r.status_code, r.text[:200])
        return None
    j = r.json()
    code = (j.get("language_code") or lang or "")[:2]
    return {"text": j.get("text", ""), "lang": code if code in INDIC | {"en"} else lang, "engine": "ElevenLabs Scribe", "tier": "C"}


async def _eleven_tts(text: str) -> dict | None:
    s = get_settings()
    if not s.elevenlabs_api_key:
        return None
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"https://api.elevenlabs.io/v1/text-to-speech/{s.elevenlabs_voice_id}?output_format=mp3_44100_128",
                         headers={"xi-api-key": s.elevenlabs_api_key},
                         json={"text": text, "model_id": s.elevenlabs_tts_model})
    if r.status_code >= 400:
        log.warning("ElevenLabs TTS %s: %s", r.status_code, r.text[:200])
        return None
    return {"audio_base64": base64.b64encode(r.content).decode(), "format": "mp3", "engine": "ElevenLabs", "tier": "C"}


async def _sarvam_tts(text: str, lang: str) -> dict | None:
    s = get_settings()
    if not s.sarvam_api_key:
        return None
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post("https://api.sarvam.ai/text-to-speech", headers={"api-subscription-key": s.sarvam_api_key},
                         json={"text": text, "target_language_code": BCP47.get(lang, "hi-IN"),
                               "speaker": s.sarvam_tts_speaker, "model": "bulbul:v2"})
    if r.status_code >= 400:
        log.warning("Sarvam TTS %s: %s", r.status_code, r.text[:200])
        return None
    return {"audio_base64": r.json()["audios"][0], "format": "wav", "engine": "Sarvam Bulbul", "tier": "IN"}


Engine = Literal["local", "sarvam", "elevenlabs", "gemini"]


@router.post("/stt")
async def stt(file: UploadFile = File(...), lang: str = Form("auto"), ui: str = Form("en"), engine: str = Form("local"),
              cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """lang: the app language ("auto": detect among the app language, English and Hindi). engine: "local" (default),
    or the cloud voice the person chose in Settings ("sarvam" | "elevenlabs"; needs the cloud_voice consent and
    offline mode off — otherwise the local engines answer)."""
    audio = await file.read(25 * 1024 * 1024)
    mime = file.content_type or "audio/webm"
    ui = ui if ui in INDIC | {"en"} else "en"
    res = None
    want = ui if lang in ("auto", "app") else lang
    if engine in ("sarvam", "elevenlabs") and await cloud_allowed(db, cu.account_id, "cloud_voice"):
        res = await (_sarvam_stt(audio, mime, want) if engine == "sarvam" else _eleven_stt(audio, mime, want))
        if res is None:  # the chosen engine failed: try the other one under the same consent
            res = await (_eleven_stt(audio, mime, want) if engine == "sarvam" else _sarvam_stt(audio, mime, want))
    if res is None:
        res = await asyncio.to_thread(_local_stt, audio, lang, ui)
    if res is None and await cloud_allowed(db, cu.account_id, "cloud_speech"):
        from app.speech import bhashini
        res = await _sarvam_stt(audio, mime, lang) or await bhashini.transcribe(audio, mime, want) or await _groq_stt(audio, mime, lang)
    if res is None:
        raise HTTPException(501, "No speech engine available. Use the browser's speech recognition instead.")
    return res


class TtsIn(BaseModel):
    text: str = Field(min_length=1, max_length=1500)
    lang: str = "hi"
    engine: Engine = "local"
    gender: Literal["female", "male"] | None = None


def _pcm_wav(pcm: bytes, rate: int) -> bytes:
    import io
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


async def _gemini_tts(text: str, lang: str, gender: str = "female") -> dict | None:
    """Speak a finished answer with Gemini Flash TTS. The text is already in the person's language."""
    s = get_settings()
    if not s.gemini_api_key or not text.strip():
        return None
    note = "" if lang in ("en", "auto") else f" Say this in {lang}. "
    voice_name = "Charon" if gender == "male" else (s.gemini_tts_voice or "Kore")
    body = {
        "contents": [{"parts": [{"text": note + text.strip()[:1500]}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice_name}}},
        },
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{s.gemini_tts_model}:generateContent"
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(url, params={"key": s.gemini_api_key}, json=body)
    except httpx.HTTPError as e:
        log.warning("Gemini TTS failed: %s", e)
        return None
    if r.status_code >= 400:
        log.warning("Gemini TTS %s: %s", r.status_code, r.text[:200])
        return None
    parts = (((r.json().get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
    inline = next((p.get("inlineData") or p.get("inline_data") for p in parts if p.get("inlineData") or p.get("inline_data")), None)
    if not inline or not inline.get("data"):
        return None
    raw = base64.b64decode(inline["data"])
    mime = str(inline.get("mimeType") or inline.get("mime_type") or "")
    rate = 24000
    if "rate=" in mime:
        try:
            rate = int(mime.split("rate=")[1].split(";")[0])
        except ValueError:
            rate = 24000
    wav = raw if mime.startswith("audio/wav") or raw[:4] == b"RIFF" else _pcm_wav(raw, rate)
    return {"audio_base64": base64.b64encode(wav).decode(), "format": "wav",
            "engine": s.gemini_tts_model, "tier": "G"}


@router.post("/tts")
async def tts(body: TtsIn, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    from app.security.settings import get_settings_row

    s_row = await get_settings_row(db, cu.account_id)
    gender = body.gender or getattr(s_row, "tts_voice_gender", None) or "female"
    if body.engine == "gemini" and await cloud_allowed(db, cu.account_id, "cloud_llm_live"):
        if out := await _gemini_tts(body.text, body.lang, gender):
            return out
    if body.engine in ("sarvam", "elevenlabs") and await cloud_allowed(db, cu.account_id, "cloud_voice"):
        out = await (_sarvam_tts(body.text, body.lang) if body.engine == "sarvam" else _eleven_tts(body.text))
        if out is None:
            out = await (_eleven_tts(body.text) if body.engine == "sarvam" else _sarvam_tts(body.text, body.lang))
        if out:
            return out
    local_engine = getattr(s_row, "tts_engine", None) or "auto"
    allow_en_local = (local_engine in ("auto", "kokoro") and M.kokoro_available()) or local_engine == "parler"
    if _local_ok["tts"] and (body.lang != "en" or allow_en_local):
        try:
            wav, engine = await asyncio.to_thread(M.tts, body.text, body.lang, gender=gender, engine=local_engine)
            return {"audio_base64": base64.b64encode(wav).decode(), "format": "wav", "engine": engine, "tier": "L"}
        except Exception as e:  # noqa: BLE001 — fall through to cloud / browser
            log.warning("local TTS failed: %s", e)
    if await cloud_allowed(db, cu.account_id, "cloud_speech"):
        from app.speech import bhashini
        if out := await _sarvam_tts(body.text, body.lang):
            return out
        if out := await bhashini.synthesize(body.text, body.lang):
            return out
    raise HTTPException(501, "No server TTS available; use browser speech synthesis")


@router.get("/voice/engines")
async def engines():
    return {"stt_local": {"indic_conformer": _local_ok["conformer"], "faster_whisper": _local_ok["whisper"],
                          "sherpa_onnx": _local_ok["sherpa_onnx"], "vad_segmentation": True,
                          "transcript_normalization": True, "streaming_ws": True, "two_stage_refinement": True},
            "tts_local": {"engine": "kokoro-82m" if M.kokoro_available() else ("indic-parler-tts" if M.parler_enabled() else "mms-tts"),
                          "kokoro_available": M.kokoro_available(),
                          "indic_tiny_bn_available": M.indic_tiny_bn_available(),
                          "parler_enabled": M.parler_enabled(),
                          "available": _local_ok["tts"], "languages": sorted(INDIC), "english": "device voice"},
            "languages": sorted(INDIC | {"en"}),
            "cloud_voices": [e for e, k in (("sarvam", get_settings().sarvam_api_key),
                                            ("elevenlabs", get_settings().elevenlabs_api_key)) if k],
            "live_voices": (["gemini_transcribe", "gemini_translate", "gemini_talk", "gemini_flash"]
                            if get_settings().gemini_api_key else [])}


class RefineIn(BaseModel):
    raw_transcript: str = Field(min_length=1, max_length=4000)
    lang: str = "en"
    context_hint: str = Field(default="", max_length=400)
    use_llm: bool = False
    encounter_id: str | None = None
    turn_id: str | None = None


@router.post("/voice/refine")
async def refine_endpoint(body: RefineIn, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    res = await refine_transcript(
        body.raw_transcript,
        lang_hint=body.lang,
        context_hint=body.context_hint,
        use_llm=body.use_llm,
    )
    transcript_id = None
    if body.encounter_id:
        t_row = await persist_transcript(
            db, cu, encounter_id=body.encounter_id, turn_id=body.turn_id, result=res
        )
        await db.commit()
        transcript_id = t_row.id
    return {
        "transcript_id": transcript_id,
        "raw_transcript": res.raw_transcript,
        "corrected_transcript": res.corrected_transcript,
        "primary_language": res.analysis.primary_language,
        "detected_languages": res.analysis.detected_languages,
        "code_switched": res.analysis.code_switched,
        "safety_preserved": res.safety_preserved,
        "rejected_categories": res.rejected_categories,
        "needs_clarification": res.needs_clarification,
        "clarification_prompt": res.clarification_prompt,
        "refinement_model": res.refinement_model,
    }


@router.websocket("/voice/stream")
async def voice_stream(ws: WebSocket, token: str = "", lang: str = "auto", ui: str = "en", profile_id: str | None = None):
    """Streaming multilingual ASR over WebSocket (partial + final refined transcripts; zero raw audio retained)."""
    import json

    if not token:
        await ws.close(code=4401)
        return
    async with SessionLocal() as db:
        try:
            cu = await authenticate_token(token, db, x_profile_id=profile_id)
            if "core_service" not in await active_purposes(db, cu.account_id):
                await ws.close(code=4403)
                return
        except HTTPException:
            await ws.close(code=4401)
            return

    await ws.accept()
    session = StreamingSpeechSession(ui_lang=ui if ui in INDIC | {"en"} else "en", lang_mode=lang or "auto")
    fmt = "pcm"
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                chunk = msg["bytes"]
                if fmt == "webm":
                    session.append_compressed(chunk)
                else:
                    session.append_pcm(chunk)
                if len(session.pcm_chunks) >= 4 or len(session.compressed_chunks) >= 2:
                    partial = await session.transcribe(is_final=False)
                    if partial:
                        await ws.send_json(partial)
            elif msg.get("text"):
                try:
                    cmd = json.loads(msg["text"])
                except ValueError:
                    continue
                ctype = cmd.get("type")
                if ctype == "config":
                    fmt = cmd.get("format", "pcm")
                    if cmd.get("lang"):
                        session.lang_mode = str(cmd["lang"])
                    if cmd.get("ui"):
                        session.ui_lang = str(cmd["ui"])
                    if cmd.get("sample_rate"):
                        session.sample_rate = int(cmd["sample_rate"])
                elif ctype == "reset":
                    session.clear_utterance()
                elif ctype == "finalize":
                    final = await session.transcribe(is_final=True)
                    if final:
                        refined = await refine_transcript(
                            final["raw_transcript"],
                            lang_hint=final["lang"],
                            audio_conf=final.get("confidence"),
                            use_llm=bool(cmd.get("use_llm", False)),
                        )
                        final["corrected_transcript"] = refined.corrected_transcript
                        final["text"] = refined.corrected_transcript
                        final["safety_preserved"] = refined.safety_preserved
                        final["needs_clarification"] = refined.needs_clarification
                        final["clarification_prompt"] = refined.clarification_prompt or final.get("clarification_prompt")
                        await ws.send_json(final)
                    else:
                        await ws.send_json({"type": "final", "text": "", "raw_transcript": "", "corrected_transcript": "", "is_partial": False})
    except WebSocketDisconnect:
        pass


@router.websocket("/voice/live")
async def voice_live(ws: WebSocket, token: str = "", profile_id: str | None = None):
    """Gemini Live: transcribe, interpret, or talk. The API holds the Google socket."""
    from app.speech.gemini_live import serve

    await serve(ws, token=token, profile_id=profile_id)
