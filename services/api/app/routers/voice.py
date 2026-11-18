"""Speech-to-text and text-to-speech — local first.

STT: IndicConformer-600M (local, 9 Indian languages) | faster-whisper large-v3-turbo (local, English / Hinglish /
     auto-detect) → Sarvam Saarika (India-hosted) → Groq Whisper (free tier) → 501 (browser Web Speech API).
     Cloud engines only with `cloud_speech` consent and offline mode off.
TTS: MMS-TTS (local) or Indic Parler-TTS (local, TTS_ENGINE=parler) → Sarvam Bulbul (consent) → 501
     (browser speechSynthesis).
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db
from app.security.auth import CurrentUser, current_user
from app.security.settings import cloud_allowed
from app.speech import models as M

router = APIRouter(tags=["voice"])
log = logging.getLogger("sushrut.voice")

BCP47 = {"en": "en-IN", "hi": "hi-IN", "bn": "bn-IN", "ta": "ta-IN", "te": "te-IN", "mr": "mr-IN",
         "gu": "gu-IN", "kn": "kn-IN", "ml": "ml-IN", "or": "od-IN"}
INDIC = {"hi", "bn", "ta", "te", "mr", "gu", "kn", "ml", "or"}
_local_ok = {"conformer": M.available("onnxruntime") and M.available("transformers"),
             "whisper": M.available("faster_whisper"), "tts": M.available("transformers")}


def _local_stt(audio_bytes: bytes, lang: str, ui: str = "en") -> dict | None:
    """lang: a language code, or "auto" = detect which of {app language, English, Hindi} is being spoken."""
    t0 = time.time()
    try:
        audio = M.decode_audio(audio_bytes)
    except Exception as e:  # noqa: BLE001
        log.warning("audio decode failed: %s", e)
        return None
    if len(audio) < 1600:  # < 0.1 s
        return {"text": "", "lang": ui if lang == "auto" else lang, "engine": "none (no speech)", "tier": "L", "seconds": 0}
    conf = None
    if lang == "auto":
        lang, conf = ui, 0.0
        if _local_ok["whisper"]:
            try:
                lang, conf = M.detect_spoken_language(audio, [ui, "en", "hi"], fallback=ui)
            except Exception as e:  # noqa: BLE001
                log.warning("language detection failed: %s", e)
    res = _transcribe(audio, lang, t0)
    if res is not None:
        res["confidence"] = conf
    return res


def _transcribe(audio, lang: str, t0: float) -> dict | None:
    if lang in INDIC and _local_ok["conformer"]:
        try:
            return {"text": M.stt_indic(audio, lang), "lang": lang, "engine": "IndicConformer-600M (local)",
                    "tier": "L", "seconds": round(time.time() - t0, 2)}
        except Exception as e:  # noqa: BLE001
            log.warning("IndicConformer failed: %s", e)
    if _local_ok["whisper"]:
        try:
            text, detected = M.stt_whisper(audio, lang)
            return {"text": text, "lang": M.LANG_ALIASES.get(detected, detected) if lang not in INDIC else lang,
                    "engine": "faster-whisper large-v3-turbo (local)", "tier": "L",
                    "seconds": round(time.time() - t0, 2)}
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
    if lang not in ("auto", "or"):
        data["language"] = lang
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post("https://api.groq.com/openai/v1/audio/transcriptions",
                         headers={"Authorization": f"Bearer {s.groq_api_key}"},
                         files={"file": ("audio.webm", audio, mime)}, data=data)
    if r.status_code >= 400:
        log.warning("Groq STT %s: %s", r.status_code, r.text[:200])
        return None
    return {"text": r.json().get("text", ""), "lang": lang, "engine": "Groq Whisper large-v3", "tier": "C"}


@router.post("/stt")
async def stt(file: UploadFile = File(...), lang: str = Form("auto"), ui: str = Form("en"),
              cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """lang="auto" (default): detect the spoken language among the app language, English and Hindi."""
    audio = await file.read(25 * 1024 * 1024)
    mime = file.content_type or "audio/webm"
    ui = ui if ui in INDIC | {"en"} else "en"
    res = await asyncio.to_thread(_local_stt, audio, lang, ui)
    if res is None and await cloud_allowed(db, cu.account_id, "cloud_speech"):
        res = await _sarvam_stt(audio, mime, lang) or await _groq_stt(audio, mime, lang)
    if res is None:
        raise HTTPException(501, "No speech engine available. Use the browser's speech recognition instead.")
    return res


class TtsIn(BaseModel):
    text: str = Field(min_length=1, max_length=1500)
    lang: str = "hi"


@router.post("/tts")
async def tts(body: TtsIn, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    if _local_ok["tts"]:
        try:
            wav, engine = await asyncio.to_thread(M.tts, body.text, body.lang)
            return {"audio_base64": base64.b64encode(wav).decode(), "format": "wav", "engine": engine, "tier": "L"}
        except Exception as e:  # noqa: BLE001 — fall through to cloud / browser
            log.warning("local TTS failed: %s", e)
    key = get_settings().sarvam_api_key
    if key and await cloud_allowed(db, cu.account_id, "cloud_speech"):
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post("https://api.sarvam.ai/text-to-speech", headers={"api-subscription-key": key},
                             json={"text": body.text, "target_language_code": BCP47.get(body.lang, "hi-IN"),
                                   "speaker": "anushka", "model": "bulbul:v2"})
        if r.status_code < 400:
            return {"audio_base64": r.json()["audios"][0], "format": "wav", "engine": "Sarvam Bulbul", "tier": "IN"}
    raise HTTPException(501, "No server TTS available; use browser speech synthesis")


@router.get("/voice/engines")
async def engines():
    return {"stt_local": {"indic_conformer": _local_ok["conformer"], "faster_whisper": _local_ok["whisper"]},
            "tts_local": {"engine": "indic-parler-tts" if M.parler_enabled() else "mms-tts", "available": _local_ok["tts"], "languages": sorted(INDIC), "english": "device voice"},
            "languages": sorted(INDIC | {"en"})}
