import asyncio

import pytest

from app.llm import orchestrator as O
from app.speech import models as M


def test_pivot_languages():
    assert "pa" in O.PIVOT_LANGS and "ur" in O.PIVOT_LANGS and "as" in O.PIVOT_LANGS
    assert "en" not in O.PIVOT_LANGS and "hi" not in O.PIVOT_LANGS


def test_from_devanagari_maps_scripts():
    assert M.from_devanagari("नमस्ते", "pa") == "ਨਮਸ੍ਤੇ"
    assert M.from_devanagari("पल ।", "ta") == "பல ."  # Tamil ends sentences with "."
    assert M.from_devanagari("बल", "ta") is None  # no Tamil letter for "ब": the caller must not show it
    assert M.from_devanagari("व", "as") == "ৱ" and M.from_devanagari("व", "bn") == "ব"
    assert M.from_devanagari("hello 108", "kn") == "hello 108"


def test_translate_answer_fills_every_read_field(monkeypatch):
    seen = {}

    def fake(texts, src, tgt, **kw):
        seen["texts"] = texts
        return [f"<{tgt}>{t}" for t in texts]
    monkeypatch.setattr(M, "translate_many", fake)
    monkeypatch.setattr(M, "available", lambda m: True)
    st = {"reply": "Fever for **2 days** is often viral.\n\nRest well.", "self_care": ["Rest"], "watch_for": ["Stiff neck"],
          "specialist": "General Physician",
          "possible_conditions": [{"name": "Viral fever", "name_en": "Viral fever", "urgency": "self_care",
                                   "supporting": ["fever"], "against": []}]}
    assert asyncio.run(O.translate_answer(st, "pa"))
    assert st["reply"].startswith("<pa>Fever for 2 days")  # bold markers removed before translation
    assert st["self_care"] == ["<pa>Rest"] and st["watch_for"] == ["<pa>Stiff neck"]
    pc = st["possible_conditions"][0]
    assert pc["name"] == "<pa>Viral fever" and pc["name_en"] == "Viral fever" and pc["urgency"] == "self_care"
    assert pc["supporting"] == ["<pa>fever"] and st["specialist"] == "General Physician"


def test_translate_answer_failure_keeps_english(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no model")
    monkeypatch.setattr(M, "translate_many", boom)
    monkeypatch.setattr(M, "available", lambda m: True)
    st = {"reply": "Rest well.", "tips": ["Drink water"]}
    assert asyncio.run(O.translate_answer(st, "ur")) is False
    assert st == {"reply": "Rest well.", "tips": ["Drink water"]}


def test_to_devanagari_round_trip():
    for lang, word in [("pa", "ਬੁਖਾਰ"), ("ta", "காய்ச்சல்"), ("bn", "জ্বর"), ("ml", "പനി"), ("or", "ଜ୍ୱର")]:
        d = M.to_devanagari(word, lang)
        assert all(0x0900 <= ord(c) <= 0x097F for c in d), (lang, d)
        assert M.from_devanagari(d, lang) == word
    assert M.to_devanagari("জ্বৰ", "as") == "ज्बर"  # Bengali script has one letter for b and v
    assert M.to_devanagari("بخار", "ur") == "بخار"


def test_protect_restore():
    s, kept = M._protect("Drink ORS and check BP; call 108.")
    assert "ORS" not in s and "108" not in s and kept == ["ORS", "BP", "108"]
    assert M._restore(s, kept) == "Drink ORS and check BP; call 108."
    assert M._restore("ਪੀਓ 9001 ਅਤੇ 9999", ["ORS"]) == "ਪੀਓ ORS ਅਤੇ "  # an unknown token is dropped, never shown


def test_guard_reads_the_english_translation_for_pivot_languages(monkeypatch):
    """An Urdu question about a cough must not be blocked because the guard model misreads Urdu script."""
    from app.safety import guard

    seen = {}

    async def fake_to_english(text, lang):
        return "I have had a cough and sore throat for three days, what should I do?"

    async def fake_guard(text, lang="en"):
        seen["text"], seen["lang"] = text, lang
        return guard.GuardResult(True, ["S2"], "blocked", [])

    async def fake_profile(db, cu):
        return "", None
    monkeypatch.setattr(O, "to_english", fake_to_english)
    monkeypatch.setattr(O.guard, "check_user_message", fake_guard)
    monkeypatch.setattr(O, "profile_summary", fake_profile)
    monkeypatch.setattr(M, "translate_many", lambda texts, s, t, **k: [f"<{t}>{x}" for x in texts])
    r = asyncio.run(O.answer(None, None, "مجھے تین دن سے کھانسی اور گلے میں درد ہے، کیا کروں؟", lang="ur"))
    assert seen == {"text": "I have had a cough and sore throat for three days, what should I do?", "lang": "en"}
    assert r.reply == "<ur>blocked"  # a block message is shown in the person's language
