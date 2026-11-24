"""Model providers with privacy-tier enforcement.

Tier L  = local (Ollama on this PC)       -> may receive PHI
Tier IN = India-hosted free APIs          -> de-identified + consent (speech/translation)
Tier C  = global free APIs (Groq, …)      -> de-identified + `cloud_llm_deidentified` consent, never images
"""
from __future__ import annotations

import json
import logging
import time
from contextvars import ContextVar
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from app.config import get_settings
from app.security.pii import scrub

log = logging.getLogger("sushrut.llm")


class ProviderUnavailable(RuntimeError):
    """The model could not answer. `hint` is a plain-language next step for the person using the app."""
    hint = "The local AI is not running. Start it with: python main.py (or start Ollama), then try again."


class ModelBusy(ProviderUnavailable):
    """The model is running but did not answer in time — usually because it is busy with another request
    (one GPU, one request at a time), or the computer is under heavy load."""
    hint = "The local AI is busy with another request right now. Please try again in a minute."


class ModelMissing(ProviderUnavailable):
    hint = "A required AI model is not downloaded yet. Run: python main.py --pull"


class PrivacyViolation(RuntimeError):
    pass


@dataclass
class LlmResult:
    text: str
    provider: str
    model: str
    tier: str
    latency_ms: int
    redactions: dict[str, int] | None = None

    def json(self) -> Any:
        return parse_json_loose(self.text)


def parse_json_loose(text: str) -> Any:
    """Parse JSON even if the model wrapped it in ``` fences or added prose."""
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("\n") + 1:] if "\n" in t else t
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        start, end = t.find("{"), t.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(t[start:end + 1])
            except json.JSONDecodeError:
                pass
        salvaged = _salvage_truncated(t)
        if salvaged is not None:
            return salvaged
        raise


def _salvage_truncated(t: str) -> dict | None:
    """Recover the complete leading part of JSON cut off by a token limit: close open strings/brackets
    after the last complete value. Returns None if nothing usable."""
    start = t.find("{")
    if start < 0:
        return None
    t = t[start:]
    for cut in range(len(t), 0, -1):
        if t[cut - 1] not in "}],\"0123456789el":  # plausible end of a complete value
            continue
        frag = t[:cut].rstrip().rstrip(",")
        stack, in_str, esc = [], False, False
        for ch in frag:
            if in_str:
                esc = (ch == "\\") and not esc
                if ch == '"' and not esc:
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch in "{[":
                stack.append("}" if ch == "{" else "]")
            elif ch in "}]" and stack:
                stack.pop()
        if in_str:
            continue
        try:
            obj = json.loads(frag + "".join(reversed(stack)))
            obj["_truncated"] = True
            return obj
        except json.JSONDecodeError:
            continue
    return None


def _raise_for_server_error(model: str, r: httpx.Response) -> None:
    """Ollama answers 5xx when a model fails to load or crashes mid-request — on a 6 GB GPU almost always because
    another model is being swapped in/out. Retrying shortly usually works, so tell the person exactly that."""
    if r.status_code >= 500:
        raise ModelBusy(f"Ollama error {r.status_code} for '{model}': {r.text[:200]}")


class Ollama:
    tier = "L"
    name = "ollama"

    def __init__(self) -> None:
        s = get_settings()
        self.base = s.ollama_base_url.rstrip("/")
        self.timeout = s.llm_timeout_s

    async def running(self) -> list[dict]:
        """Models Ollama holds in memory now, with their size in VRAM (GB)."""
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                r = await c.get(f"{self.base}/api/ps")
                r.raise_for_status()
                return [{"name": m["name"], "vram_gb": round(m.get("size_vram", 0) / 1e9, 1), "until": m.get("expires_at")}
                        for m in r.json().get("models", [])]
        except httpx.HTTPError:
            return []

    async def models(self) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                r = await c.get(f"{self.base}/api/tags")
                r.raise_for_status()
                return [m["name"] for m in r.json().get("models", [])]
        except httpx.HTTPError:
            return []

    async def chat(self, model: str, messages: list[dict], *, schema: dict | None = None,
                   images: list[str] | None = None, temperature: float = 0.2,
                   num_ctx: int = 8192, num_predict: int | None = None,
                   repeat_penalty: float | None = None,
                   on_token: Callable[[str], None] | None = None) -> LlmResult:
        """on_token: when given, the reply is streamed and on_token receives the whole text so far after each
        chunk (the result is identical to the non-streamed call)."""
        msgs = [dict(m) for m in messages]
        if images:
            msgs[-1]["images"] = images  # base64 strings, attached to the last user turn
        body: dict[str, Any] = {"model": model, "messages": msgs, "stream": on_token is not None,
                                "options": {"temperature": temperature, "num_ctx": num_ctx}}
        if num_predict:
            body["options"]["num_predict"] = num_predict
        if repeat_penalty:
            # Small models can loop on a whole sentence; Ollama's default 64-token window is too short to catch it.
            body["options"].update(repeat_penalty=repeat_penalty, repeat_last_n=512)
        if schema is not None:
            body["format"] = schema
        if model.startswith("qwen3:"):  # qwen3 text models "think" aloud by default; we want the answer only
            body["think"] = False
        s_ = get_settings()
        body["keep_alive"] = s_.keep_alive_vision if (images or "vl" in model) else s_.keep_alive_chat
        t0 = time.perf_counter()
        if on_token is not None:
            return await self._chat_streamed(model, body, on_token, t0)
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.post(f"{self.base}/api/chat", json=body)
        except httpx.TimeoutException as e:
            raise ModelBusy(f"Model '{model}' did not answer within {self.timeout:.0f}s (busy with another request, "
                            f"running on CPU, or generating runaway output)") from e
        except httpx.HTTPError as e:
            raise ProviderUnavailable(f"Ollama not reachable at {self.base}: {e}") from e
        if r.status_code == 404:
            raise ModelMissing(f"Model '{model}' not pulled. Run: ollama pull {model}")
        _raise_for_server_error(model, r)
        r.raise_for_status()
        return LlmResult(r.json()["message"]["content"], self.name, model, self.tier,
                         int((time.perf_counter() - t0) * 1000))

    async def _chat_streamed(self, model: str, body: dict, on_token: Callable[[str], None], t0: float) -> LlmResult:
        """Same request with stream=true. Closing the connection (task cancelled) stops generation in Ollama."""
        text = ""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                async with c.stream("POST", f"{self.base}/api/chat", json=body) as r:
                    if r.status_code == 404:
                        raise ModelMissing(f"Model '{model}' not pulled. Run: ollama pull {model}")
                    if r.status_code >= 500:
                        await r.aread()
                    _raise_for_server_error(model, r)
                    r.raise_for_status()
                    async for line in r.aiter_lines():
                        if not line:
                            continue
                        chunk = json.loads(line)
                        if tok := chunk.get("message", {}).get("content"):
                            text += tok
                            on_token(text)
                        if chunk.get("done"):
                            break
        except httpx.TimeoutException as e:
            raise ModelBusy(f"Model '{model}' did not answer within {self.timeout:.0f}s") from e
        except httpx.HTTPError as e:
            raise ProviderUnavailable(f"Ollama not reachable at {self.base}: {e}") from e
        return LlmResult(text, self.name, model, self.tier, int((time.perf_counter() - t0) * 1000))

    async def stream(self, model: str, messages: list[dict], *, temperature: float = 0.3,
                     num_ctx: int = 8192) -> AsyncIterator[str]:
        body = {"model": model, "messages": messages, "stream": True,
                "options": {"temperature": temperature, "num_ctx": num_ctx}}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                async with c.stream("POST", f"{self.base}/api/chat", json=body) as r:
                    r.raise_for_status()
                    async for line in r.aiter_lines():
                        if not line:
                            continue
                        chunk = json.loads(line)
                        if tok := chunk.get("message", {}).get("content"):
                            yield tok
                        if chunk.get("done"):
                            break
        except httpx.HTTPError as e:
            raise ProviderUnavailable(f"Ollama stream failed: {e}") from e

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.post(f"{self.base}/api/embed", json={"model": model, "input": texts})
        except httpx.HTTPError as e:
            raise ProviderUnavailable(str(e)) from e
        if r.status_code == 404:
            raise ProviderUnavailable(f"Embedding model '{model}' not pulled. Run: ollama pull {model}")
        r.raise_for_status()
        return r.json()["embeddings"]


def _cloud_error(name: str, status: int, body: str) -> ProviderUnavailable:
    """Every cloud HTTP failure is ProviderUnavailable, so the caller can fall back to the local model."""
    if status == 429:
        e = ProviderUnavailable(f"{name} rate limit or quota reached")
        e.hint = (f"{name}'s free quota is used up for now. Try again later, or choose a model on this device.")
        return e
    e = ProviderUnavailable(f"{name} error {status}: {body[:200]}")
    e.hint = f"{name} could not answer ({status}). Try again in a minute, or choose a model on this device."
    return e


def _network_error(name: str, exc: Exception) -> ProviderUnavailable:
    if isinstance(exc, httpx.TimeoutException):
        e = ProviderUnavailable(f"{name} did not answer in time")
        e.hint = f"{name} took too long. Try again, or choose a model on this device."
        return e
    e = ProviderUnavailable(f"{name} is unreachable: {exc}")
    e.hint = f"{name} could not be reached. Check the connection, or choose a model on this device."
    return e


def _response_formats(schema: dict | None) -> list[dict | None]:
    """Try the real schema first. A 400 retries as a plain JSON object (Groq and Gemini both reject some schemas)."""
    if schema is None:
        return [None]
    return [
        {"type": "json_schema", "json_schema": {"name": "answer", "strict": False, "schema": schema}},
        {"type": "json_object"},
    ]


async def _read_openai_stream(response: httpx.Response, on_token: Callable[[str], None]) -> str:
    """OpenAI-style server-sent chunks. on_token receives the whole text so far, matching Ollama."""
    text = ""
    async for line in response.aiter_lines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            if data == "[DONE]":
                break
            continue
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            continue
        choice = (chunk.get("choices") or [{}])[0]
        tok = (choice.get("delta") or {}).get("content") or ""
        if tok:
            text += tok
            on_token(text)
        if choice.get("finish_reason"):
            break
    return text


async def _openai_chat(name: str, url: str, key: str, model: str, messages: list[dict], *,
                       schema: dict | None, temperature: float, num_predict: int | None,
                       on_token: Callable[[str], None] | None, timeout: float) -> str:
    headers = {"Authorization": f"Bearer {key}"}
    formats = _response_formats(schema)
    last: ProviderUnavailable | None = None
    for i, fmt in enumerate(formats):
        body: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature,
                                "stream": on_token is not None}
        if fmt is not None:
            body["response_format"] = fmt
        if num_predict:
            body["max_tokens"] = num_predict
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                if on_token is not None:
                    async with c.stream("POST", url, json=body, headers=headers) as r:
                        if r.status_code == 400 and schema is not None and i < len(formats) - 1:
                            await r.aread()
                            continue
                        if r.status_code >= 400:
                            await r.aread()
                            raise _cloud_error(name, r.status_code, r.text)
                        return await _read_openai_stream(r, on_token)
                r = await c.post(url, json=body, headers=headers)
        except ProviderUnavailable:
            raise
        except httpx.HTTPError as e:
            raise _network_error(name, e) from e
        if r.status_code == 400 and schema is not None and i < len(formats) - 1:
            last = _cloud_error(name, r.status_code, r.text)
            continue
        if r.status_code >= 400:
            raise _cloud_error(name, r.status_code, r.text)
        return r.json()["choices"][0]["message"]["content"]
    raise last or ProviderUnavailable(f"{name} rejected the request")


class Groq:
    """Groq free tier (OpenAI-compatible). Tier C — only de-identified text, never images."""

    tier = "C"
    name = "groq"
    base = "https://api.groq.com/openai/v1"

    def __init__(self) -> None:
        self.key = get_settings().groq_api_key

    @property
    def available(self) -> bool:
        return bool(self.key)

    async def chat(self, model: str, messages: list[dict], *, schema: dict | None = None,
                   images: list[str] | None = None, temperature: float = 0.2,
                   num_predict: int | None = None, on_token: Callable[[str], None] | None = None,
                   **_: Any) -> LlmResult:
        if not self.available:
            raise ProviderUnavailable("GROQ_API_KEY not set")
        if images:
            raise PrivacyViolation("Images are never sent to Tier C providers")
        clean, total = [], {}
        for m in messages:
            raw = m.get("content")
            if not isinstance(raw, str):
                raw = json.dumps(raw, ensure_ascii=False)
            text, counts = scrub(raw)
            for k, v in counts.items():
                total[k] = total.get(k, 0) + v
            clean.append({"role": m["role"], "content": text})
        t0 = time.perf_counter()
        text = await _openai_chat("Groq", f"{self.base}/chat/completions", self.key, model, clean,
                                  schema=schema, temperature=temperature, num_predict=num_predict,
                                  on_token=on_token, timeout=60)
        return LlmResult(text, self.name, model, self.tier, int((time.perf_counter() - t0) * 1000), total)


class Gemini:
    """Google Gemini through its OpenAI-compatible endpoint. Chosen explicitly by the person ("Gemini" answer
    model) with the `cloud_llm_full` consent: it receives the question, the needed health context and photos
    as they are (the person's choice; offline mode still blocks it)."""

    tier = "G"
    name = "gemini"
    base = "https://generativelanguage.googleapis.com/v1beta/openai"

    def __init__(self) -> None:
        self.key = get_settings().gemini_api_key

    @property
    def available(self) -> bool:
        return bool(self.key)

    async def chat(self, model: str, messages: list[dict], *, schema: dict | None = None,
                   images: list[str] | None = None, temperature: float = 0.2,
                   num_predict: int | None = None, on_token: Callable[[str], None] | None = None,
                   **_: Any) -> LlmResult:
        if not self.available:
            raise ProviderUnavailable("GEMINI_API_KEY not set")
        msgs = [{"role": m["role"], "content": m["content"]} for m in messages]
        if images:
            last = msgs[-1]
            text = last["content"] if isinstance(last["content"], str) else json.dumps(last["content"], ensure_ascii=False)
            last["content"] = [{"type": "text", "text": text}] + [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b}"}} for b in images]
        t0 = time.perf_counter()
        text = await _openai_chat("Gemini", f"{self.base}/chat/completions", self.key, model, msgs,
                                  schema=schema, temperature=temperature, num_predict=num_predict,
                                  on_token=on_token, timeout=90)
        return LlmResult(text, self.name, model, self.tier, int((time.perf_counter() - t0) * 1000), {})


# Set per request by the routers: the person chose Gemini and gave the `cloud_llm_full` consent (offline mode off)
full_cloud_allowed: ContextVar[bool] = ContextVar("full_cloud_allowed", default=False)

_ollama: Ollama | None = None
_groq: Groq | None = None
_gemini: Gemini | None = None


def gemini() -> Gemini:
    global _gemini
    _gemini = _gemini or Gemini()
    return _gemini


def ollama() -> Ollama:
    global _ollama
    _ollama = _ollama or Ollama()
    return _ollama


def groq() -> Groq:
    global _groq
    _groq = _groq or Groq()
    return _groq


def gemini_model_for(pref: str) -> str | None:
    """Gemini model id when this request may send the question, health context and photos. None otherwise."""
    if pref not in ("gemini", "gemini_fast"):
        return None
    if not full_cloud_allowed.get() or not gemini().available:
        return None
    s = get_settings()
    return s.gemini_fast_model if pref == "gemini_fast" else s.gemini_model


def model_options() -> list[str]:
    """Answer-speed choices offered in Settings. 'cloud' is listed but still needs consent per request."""
    s = get_settings()
    return (["auto"] + ["balanced"] + (["fast"] if s.fast_model_enabled else []) + (["instant"] if s.instant_model_enabled else [])
            + (["cloud"] if s.groq_api_key else []) + (["gemini", "gemini_fast"] if s.gemini_api_key else []))


def doc_model_for(pref: str, kind: str = "prescription") -> str:
    """Document reader for the person's answer-speed choice and the document kind ("prescription" | "lab_report"):
    the smaller reader only when "Faster" is on and one passed its gate for that kind; otherwise the default."""
    s = get_settings()
    fast = s.fast_lab_vlm_model if kind == "lab_report" else s.fast_rx_vlm_model
    instant = s.instant_lab_vlm_model if kind == "lab_report" else s.instant_rx_vlm_model
    if pref == "instant" and s.instant_model_enabled:
        return instant or fast or s.rx_vlm_model  # quickest reader that passed for this kind
    if pref == "fast" and s.fast_model_enabled and fast:
        return fast
    return s.rx_vlm_model


def text_model_for(pref: str) -> tuple[str, int | None]:
    """(local model, output cap) for a user's answer-speed choice. Unknown / disabled choices fall back to balanced."""
    s = get_settings()
    if pref == "instant" and s.instant_model_enabled:
        return (s.instant_llm_model or s.fast_llm_model), 500  # quickest chat model that passed; short answers
    if pref == "fast" and s.fast_model_enabled:
        return s.fast_llm_model, 800
    return s.llm_model, None


async def clinical_chat(messages: list[dict], *, schema: dict | None = None,
                        images: list[str] | None = None, allow_cloud: bool = False,
                        prefer_cloud: bool = False, num_predict: int | None = None,
                        repeat_penalty: float | None = None,
                        on_token: Callable[[str], None] | None = None, model_pref: str = "balanced") -> LlmResult:
    """Answer with the model the person chose.

    Gemini only when model_pref is gemini or gemini_fast and this request set full_cloud_allowed (consent).
    Groq ("cloud" / prefer_cloud) only with allow_cloud, and only de-identified text — photos stay on the local
    vision model. A cloud failure falls back to the local model instead of failing the turn.
    """
    s = get_settings()
    if (g_model := gemini_model_for(model_pref)):
        try:
            return await gemini().chat(g_model, messages, schema=schema, images=images,
                                       num_predict=num_predict, on_token=on_token)
        except ProviderUnavailable as e:
            log.warning("Gemini unavailable (%s); answering locally", e)
        model_pref = "balanced"
    elif model_pref in ("gemini", "gemini_fast"):
        model_pref = "balanced"  # chosen but not allowed / no key: the careful local model answers
    prefer_cloud = prefer_cloud or model_pref == "cloud"
    if images:
        model = s.vlm_model
    else:
        model, cap = text_model_for(model_pref)
        if cap:
            num_predict = min(num_predict or cap, cap)
    if prefer_cloud and allow_cloud and not images and groq().available:
        try:
            return await groq().chat(s.groq_llm_model, messages, schema=schema,
                                     num_predict=num_predict, on_token=on_token)
        except ProviderUnavailable as e:
            log.warning("Groq unavailable (%s); answering locally", e)
    try:
        return await ollama().chat(model, messages, schema=schema, images=images, num_predict=num_predict,
                                   repeat_penalty=repeat_penalty, on_token=on_token)
    except ProviderUnavailable as e:
        if not images and model != s.llm_model:
            # the "Faster" model failed (seen in the safety eval: it looped until Ollama aborted with "token repeat
            # limit reached") — answer with the careful model instead of showing the person an error
            log.warning("fast model %s failed (%s); retrying with %s", model, e, s.llm_model)
            return await clinical_chat(messages, schema=schema, allow_cloud=allow_cloud, prefer_cloud=False,
                                       num_predict=num_predict, repeat_penalty=repeat_penalty, on_token=on_token,
                                       model_pref="balanced")
        if allow_cloud and not images and groq().available and not prefer_cloud:
            # local model is down: Groq is the consented de-identified fallback, not a second try after Groq itself failed
            return await groq().chat(s.groq_llm_model, messages, schema=schema, num_predict=num_predict)
        raise
