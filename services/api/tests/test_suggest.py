"""Suggestion chips: rule candidates, filters, model fallback."""
import asyncio
import re

from app.llm import suggest as sg


def test_rules_follow_the_last_answer_and_own_data():
    c = sg.Ctx(lang="en", conditions=["Hypertension"], bp="150/95", abnormal_labs=["HbA1c"],
               last_kind="symptoms", last_conditions=["Dengue"], last_watch=False)
    out = sg.rule_candidates(c)
    assert out[0] == "What causes Dengue?"
    assert "Is my BP 150/95 too high?" in out and "What does my HbA1c result mean?" in out
    assert "Which signs mean I should go to hospital?" in out
    assert len(out) == len(set(out))


def test_always_enough_candidates_and_hindi():
    assert len(sg.rule_candidates(sg.Ctx(lang="en"))) >= 4
    hi = sg.rule_candidates(sg.Ctx(lang="hi", last_conditions=["डेंगू"], last_kind="symptoms"))
    assert hi[0] == "डेंगू क्यों होता है?" and all(re.search(r"[ऀ-ॿ]", x) for x in hi)


def test_clean_filters_medicines_diagnosis_language_length():
    rx = re.compile(r"(?i)\b(paracetamol|dolo)\b")
    items = ["Can I take paracetamol?", "You have dengue", "x", "What causes fever?", "what causes fever?",
             "1. How long does it last?", 42]
    assert sg.clean(items, rx, "en") == ["What causes fever?", "How long does it last?"]
    assert sg.clean(["What causes fever?", "बुखार क्यों होता है?"], rx, "hi") == ["बुखार क्यों होता है?"]


def test_model_failure_falls_back_to_rules(monkeypatch):
    async def none(*a, **k):
        return None
    monkeypatch.setattr(sg, "model_pick", none)
    r = asyncio.run(sg.suggest(sg.Ctx(lang="en", last_conditions=["Dengue"], last_kind="symptoms"), "tiny", None))
    assert r["source"] == "rules" and r["items"][0] == "What causes Dengue?" and len(r["items"]) == 4


def test_model_output_is_filtered_and_topped_up(monkeypatch):
    async def fake(*a, **k):
        return ["Is dengue dangerous?", "Take paracetamol now?", "Is dengue dangerous?"]
    monkeypatch.setattr(sg, "model_pick", fake)
    rx = re.compile(r"(?i)\bparacetamol\b")
    r = asyncio.run(sg.suggest(sg.Ctx(lang="en", last_question="high fever", last_conditions=["Dengue"], last_kind="symptoms"), "tiny", rx))
    assert r["source"] == "model" and r["items"][0] == "Is dengue dangerous?" and len(r["items"]) == 4
    assert not any("paracetamol" in x.lower() for x in r["items"])


async def test_suggestions_route(client, guest):
    r = await client.get("/chat/suggestions?lang=hi", headers=guest)
    assert r.status_code == 200 and len(r.json()["items"]) == 4


def test_questions_to_the_patient_are_dropped():
    out = sg.clean(["Have you had any recent illnesses?", "What type of clothing do you usually wear?",
                    "How long will my fever last?", "Can you explain my report?", "क्या आपको बुखार है?"], None, "en")
    assert out == ["How long will my fever last?", "Can you explain my report?"]


def test_no_conversation_uses_the_persons_data_not_the_model(monkeypatch):
    called = []

    async def fake(*a, **k):
        called.append(1)
        return ["What type of clothing do you usually wear?"]
    monkeypatch.setattr(sg, "model_pick", fake)
    r = asyncio.run(sg.suggest(sg.Ctx(lang="en", conditions=["Hypertension"], bp="150/95"), "tiny", None))
    assert not called and r["source"] == "rules" and "Is my BP 150/95 too high?" in r["items"]
