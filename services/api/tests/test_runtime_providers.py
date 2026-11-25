"""Idle model release, and the Gemini answer model only with consent + key (local otherwise)."""
import time

import pytest

from app.llm import providers as P


def test_release_idle_clears_only_old_caches(monkeypatch):
    from app.speech import models as M

    class FakeCache:
        def __init__(self):
            self.cleared = False

        def cache_info(self):
            return type("I", (), {"currsize": 0 if self.cleared else 1})()

        def cache_clear(self):
            self.cleared = True
    old, new = FakeCache(), FakeCache()
    monkeypatch.setattr(M, "_caches", lambda: {"it2": old, "mms": new})
    M._last_used.clear()
    M._last_used.update({"it2": time.time() - 1000, "mms": time.time()})
    assert M.release_idle(600) == ["it2"]
    assert old.cleared and not new.cleared


class _Res:
    text = '{"reply": "ok"}'


async def test_gemini_used_only_when_allowed(monkeypatch):
    calls = []

    async def fake_gemini_chat(self, model, messages, **kw):
        calls.append(("gemini", model))
        return P.LlmResult('{"reply": "from gemini"}', "gemini", model, "G", 1, {})

    async def fake_ollama_chat(self, model, messages, **kw):
        calls.append(("ollama", model))
        return P.LlmResult('{"reply": "local"}', "ollama", model, "L", 1, {})
    monkeypatch.setattr(P.Gemini, "chat", fake_gemini_chat)
    monkeypatch.setattr(P.Ollama, "chat", fake_ollama_chat)
    monkeypatch.setattr(P, "_gemini", None)
    s = P.get_settings()
    monkeypatch.setattr(s, "gemini_api_key", "test-key")

    tok = P.full_cloud_allowed.set(False)
    r = await P.clinical_chat([{"role": "user", "content": "hi"}], model_pref="gemini")
    assert r.provider == "ollama"  # chosen but no consent: local
    P.full_cloud_allowed.reset(tok)

    tok = P.full_cloud_allowed.set(True)
    r = await P.clinical_chat([{"role": "user", "content": "hi"}], model_pref="gemini")
    assert r.provider == "gemini" and calls[-1] == ("gemini", s.gemini_model)
    r = await P.clinical_chat([{"role": "user", "content": "hi"}], model_pref="gemini_fast")
    assert calls[-1] == ("gemini", s.gemini_fast_model)
    P.full_cloud_allowed.reset(tok)


async def test_cloud_pref_stays_on_groq_even_with_gemini_consent(monkeypatch):
    """Online (Groq) must not send the full question or photos to Gemini."""
    calls = []

    async def fake_gemini_chat(self, model, messages, **kw):
        calls.append(("gemini", kw.get("images")))
        return P.LlmResult('{"reply": "from gemini"}', "gemini", model, "G", 1, {})

    async def fake_groq_chat(self, model, messages, **kw):
        calls.append(("groq", kw.get("images")))
        return P.LlmResult('{"reply": "from groq"}', "groq", model, "C", 1, {"name": 1})

    async def fake_ollama_chat(self, model, messages, **kw):
        calls.append(("ollama", model))
        return P.LlmResult('{"reply": "local"}', "ollama", model, "L", 1, {})

    monkeypatch.setattr(P.Gemini, "chat", fake_gemini_chat)
    monkeypatch.setattr(P.Groq, "chat", fake_groq_chat)
    monkeypatch.setattr(P.Ollama, "chat", fake_ollama_chat)
    monkeypatch.setattr(P, "_gemini", None)
    monkeypatch.setattr(P, "_groq", None)
    s = P.get_settings()
    monkeypatch.setattr(s, "gemini_api_key", "test-key")
    monkeypatch.setattr(s, "groq_api_key", "test-key")
    tok = P.full_cloud_allowed.set(True)
    r = await P.clinical_chat([{"role": "user", "content": "fever"}], model_pref="cloud", allow_cloud=True)
    assert r.provider == "groq"
    assert calls == [("groq", None)]
    calls.clear()
    r = await P.clinical_chat([{"role": "user", "content": "photo"}], model_pref="cloud",
                              allow_cloud=True, images=["abc"])
    assert r.provider == "ollama"  # photos never go to Groq, and Online does not mean Gemini
    assert calls == [("ollama", s.vlm_model)]
    P.full_cloud_allowed.reset(tok)


async def test_groq_http_error_falls_back_to_local(monkeypatch):
    async def fake_groq_chat(self, model, messages, **kw):
        raise P.ProviderUnavailable("Groq error 500")

    async def fake_ollama_chat(self, model, messages, **kw):
        return P.LlmResult('{"reply": "local"}', "ollama", model, "L", 1, {})

    monkeypatch.setattr(P.Groq, "chat", fake_groq_chat)
    monkeypatch.setattr(P.Ollama, "chat", fake_ollama_chat)
    monkeypatch.setattr(P, "_groq", None)
    s = P.get_settings()
    monkeypatch.setattr(s, "groq_api_key", "test-key")
    r = await P.clinical_chat([{"role": "user", "content": "hi"}], model_pref="cloud", allow_cloud=True)
    assert r.provider == "ollama"
