"""Spoken-language routing: auto-detected Hindi goes to IndicConformer, English to Whisper; English is never
synthesised on the server (the device voice reads it)."""
import numpy as np
import pytest

from app.routers import voice as V
from app.speech import models as M


@pytest.fixture
def fake_engines(monkeypatch):
    calls = {}
    monkeypatch.setattr(M, "decode_audio", lambda b, sr=16000: np.zeros(16000 * 3, np.float32))
    monkeypatch.setitem(V._local_ok, "conformer", True)
    monkeypatch.setitem(V._local_ok, "whisper", True)
    monkeypatch.setattr(M, "stt_indic", lambda a, lang: calls.setdefault("indic", lang) and "मुझे बुखार है")
    monkeypatch.setattr(M, "stt_whisper", lambda a, lang: (calls.setdefault("whisper", lang) and "I have fever", lang or "en"))
    return calls


def test_hindi_spoken_to_english_app_goes_to_indic(monkeypatch, fake_engines):
    monkeypatch.setattr(M, "detect_spoken_language", lambda a, allowed, fallback: ("hi", 0.93))
    r = V._local_stt(b"x", "auto", "en")
    assert r["lang"] == "hi" and fake_engines == {"indic": "hi"} and r["confidence"] == 0.93


def test_english_spoken_to_hindi_app_goes_to_whisper(monkeypatch, fake_engines):
    monkeypatch.setattr(M, "detect_spoken_language", lambda a, allowed, fallback: ("en", 0.97))
    r = V._local_stt(b"x", "auto", "hi")
    assert r["lang"] == "en" and fake_engines == {"whisper": "en"}


def test_detection_failure_keeps_app_language(monkeypatch, fake_engines):
    def boom(*a, **k):
        raise RuntimeError("no model")
    monkeypatch.setattr(M, "detect_spoken_language", boom)
    r = V._local_stt(b"x", "auto", "hi")
    assert r["lang"] == "hi" and "indic" in fake_engines


def test_restricted_detection_maps_urdu_and_short_clips(monkeypatch):
    class Fake:
        def detect_language(self, audio):
            return "ur", 0.6, [("ur", 0.6), ("hi", 0.2), ("en", 0.1), ("cy", 0.1)]
    monkeypatch.setattr(M, "_lid", lambda: Fake())
    lang, conf = M.detect_spoken_language(np.zeros(16000 * 3, np.float32), ["en", "en", "hi"], "en")
    assert lang == "hi" and conf > 0.85
    assert M.detect_spoken_language(np.zeros(8000, np.float32), ["en", "hi"], "en") == ("en", 0.0)


def test_unsure_detection_keeps_app_language(monkeypatch):
    class Fake:
        def detect_language(self, audio):
            return "en", 0.5, [("en", 0.5), ("hi", 0.45)]
    monkeypatch.setattr(M, "_lid", lambda: Fake())
    assert M.detect_spoken_language(np.zeros(16000 * 3, np.float32), ["hi", "en", "hi"], "hi")[0] == "hi"


def test_server_never_speaks_english():
    with pytest.raises(ValueError):
        M.tts("Hello", "en")
