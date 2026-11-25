"""Bhashini (ULCA) speech fallback.

Used only when BHASHINI_USER_ID and BHASHINI_API_KEY are set and the person allowed India-hosted voice
(`cloud_speech`). Any failure returns None so another engine can answer.
"""
from __future__ import annotations

import base64
import logging
import time

import httpx

from app.config import get_settings

log = logging.getLogger("sushrut.bhashini")

_CONFIG_URL = "https://meity-auth.ulcacontrib.org/ulca/apis/v0/model/getModelsPipeline"
_CACHE: dict[tuple, tuple[float, dict]] = {}
_TTL_S = 600

# App language codes match ULCA sourceLanguage codes for the languages this app speaks.
_LANG = {
    "as": "as", "bn": "bn", "en": "en", "gu": "gu", "hi": "hi", "kn": "kn", "ml": "ml",
    "mr": "mr", "or": "or", "pa": "pa", "ta": "ta", "te": "te", "ur": "ur",
}


def _configured() -> bool:
    s = get_settings()
    return bool(s.bhashini_user_id and s.bhashini_api_key)


async def _endpoint(task: str, source: str, target: str | None = None) -> dict | None:
    """Pipeline config: callback URL, auth header, and service id. Cached for a few minutes."""
    if not _configured() or source not in _LANG:
        return None
    key = (task, source, target or "")
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _TTL_S:
        return hit[1]
    s = get_settings()
    lang = {"sourceLanguage": _LANG[source]}
    if target:
        lang["targetLanguage"] = _LANG.get(target, target)
    body: dict = {"pipelineTasks": [{"taskType": task, "config": {"language": lang}}]}
    if s.bhashini_pipeline_id:
        body["pipelineRequestConfig"] = {"pipelineId": s.bhashini_pipeline_id}
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(_CONFIG_URL, json=body, headers={
                "userID": s.bhashini_user_id, "ulcaApiKey": s.bhashini_api_key})
    except httpx.HTTPError as e:
        log.warning("Bhashini config failed: %s", e)
        return None
    if r.status_code >= 400:
        log.warning("Bhashini config %s: %s", r.status_code, r.text[:200])
        return None
    j = r.json()
    end = j.get("pipelineInferenceAPIEndPoint") or {}
    callback = end.get("callbackUrl")
    auth = (end.get("inferenceApiKey") or {})
    tasks = j.get("pipelineResponseConfig") or []
    service = ""
    for t in tasks:
        if t.get("taskType") == task:
            configs = t.get("config") or []
            if configs and isinstance(configs, list):
                service = configs[0].get("serviceId") or ""
            elif isinstance(configs, dict):
                service = configs.get("serviceId") or ""
    if not callback or not auth.get("value"):
        log.warning("Bhashini config had no inference endpoint")
        return None
    ready = {"url": callback, "header": auth.get("name") or "Authorization", "value": auth["value"],
             "serviceId": service}
    _CACHE[key] = (time.time(), ready)
    return ready


async def transcribe(audio: bytes, mime: str, lang: str) -> dict | None:
    ep = await _endpoint("asr", lang if lang not in ("auto", "app") else "hi")
    if ep is None:
        return None
    source = _LANG.get(lang, "hi")
    config = {"language": {"sourceLanguage": source}, "audioFormat": "webm", "samplingRate": 16000}
    if ep["serviceId"]:
        config["serviceId"] = ep["serviceId"]
    body = {"pipelineTasks": [{"taskType": "asr", "config": config}],
            "inputData": {"audio": [{"audioContent": base64.b64encode(audio).decode()}]}}
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(ep["url"], json=body, headers={ep["header"]: ep["value"]})
    except httpx.HTTPError as e:
        log.warning("Bhashini STT failed: %s", e)
        return None
    if r.status_code >= 400:
        log.warning("Bhashini STT %s: %s", r.status_code, r.text[:200])
        return None
    outputs = ((r.json().get("pipelineResponse") or [{}])[0].get("output") or [])
    text = (outputs[0].get("source") if outputs else "") or ""
    if not text.strip():
        return None
    return {"text": text.strip(), "lang": source, "engine": "Bhashini", "tier": "IN"}


async def synthesize(text: str, lang: str) -> dict | None:
    source = _LANG.get(lang)
    if not source or not text.strip():
        return None
    ep = await _endpoint("tts", source)
    if ep is None:
        return None
    config: dict = {"language": {"sourceLanguage": source}, "gender": "female"}
    if ep["serviceId"]:
        config["serviceId"] = ep["serviceId"]
    body = {"pipelineTasks": [{"taskType": "tts", "config": config}],
            "inputData": {"input": [{"source": text[:1500]}]}}
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(ep["url"], json=body, headers={ep["header"]: ep["value"]})
    except httpx.HTTPError as e:
        log.warning("Bhashini TTS failed: %s", e)
        return None
    if r.status_code >= 400:
        log.warning("Bhashini TTS %s: %s", r.status_code, r.text[:200])
        return None
    audio = ((r.json().get("pipelineResponse") or [{}])[0].get("audio") or [])
    content = (audio[0].get("audioContent") if audio else "") or ""
    if not content:
        return None
    return {"audio_base64": content, "format": "wav", "engine": "Bhashini", "tier": "IN"}
