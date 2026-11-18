"""Model providers with privacy-tier enforcement.

Tier L  = local (Ollama on this PC)       -> may receive PHI
Tier IN = India-hosted free APIs          -> de-identified + consent (speech/translation)
Tier C  = global free APIs (Groq, …)      -> de-identified + `cloud_llm_deidentified` consent, never images
"""
from __future__ import annotations

import json
import logging
import time
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
                   images: list[str] | None = None, temperature: float = 0.2, **_: Any) -> LlmResult:
        if not self.available:
            raise ProviderUnavailable("GROQ_API_KEY not set")
        if images:
            raise PrivacyViolation("Images are never sent to Tier C providers")
        clean, total = [], {}
        for m in messages:
            text, counts = scrub(m["content"])
            for k, v in counts.items():
                total[k] = total.get(k, 0) + v
            clean.append({"role": m["role"], "content": text})
        body: dict[str, Any] = {"model": model, "messages": clean, "temperature": temperature}
        if schema is not None:
            body["response_format"] = {"type": "json_object"}
        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{self.base}/chat/completions", json=body,
                             headers={"Authorization": f"Bearer {self.key}"})
        if r.status_code == 429:
            raise ProviderUnavailable("Groq free-tier rate limit reached")
        r.raise_for_status()
        return LlmResult(r.json()["choices"][0]["message"]["content"], self.name, model, self.tier,
                         int((time.perf_counter() - t0) * 1000), total)


_ollama: Ollama | None = None
_groq: Groq | None = None


def ollama() -> Ollama:
    global _ollama
    _ollama = _ollama or Ollama()
    return _ollama


def groq() -> Groq:
    global _groq
    _groq = _groq or Groq()
    return _groq


def model_options() -> list[str]:
    """Answer-speed choices offered in Settings. 'cloud' is listed but still needs consent per request."""
    s = get_settings()
    return (["auto"] + ["balanced"] + (["fast"] if s.fast_model_enabled else []) + (["instant"] if s.instant_model_enabled else [])
            + (["cloud"] if s.groq_api_key else []))


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
    """Local first. Cloud (Tier C) only when explicitly allowed by consent AND requested/needed.
    model_pref: the user's answer-speed choice ('cloud' = prefer the cloud model when consent allows; photos always
    go to the local vision model)."""
    s = get_settings()
    prefer_cloud = prefer_cloud or model_pref == "cloud"
    if images:
        model = s.vlm_model
    else:
        model, cap = text_model_for(model_pref)
        if cap:
            num_predict = min(num_predict or cap, cap)
    if prefer_cloud and allow_cloud and not images and groq().available:
        try:
            return await groq().chat(s.groq_llm_model, messages, schema=schema)
        except ProviderUnavailable:
            pass
    try:
        return await ollama().chat(model, messages, schema=schema, images=images, num_predict=num_predict,
                                   repeat_penalty=repeat_penalty, on_token=on_token)
    except ProviderUnavailable as e:
        if not images and model != s.llm_model:
            # the "Faster" model failed (seen in the safety eval: it looped until Ollama aborted with "token repeat
            # limit reached") — answer with the careful model instead of showing the person an error
            log.warning("fast model %s failed (%s); retrying with %s", model, e, s.llm_model)
            return await clinical_chat(messages, schema=schema, allow_cloud=allow_cloud, prefer_cloud=prefer_cloud,
                                       num_predict=num_predict, repeat_penalty=repeat_penalty, on_token=on_token,
                                       model_pref="balanced")
        if allow_cloud and not images and groq().available:
            return await groq().chat(s.groq_llm_model, messages, schema=schema)
        raise
