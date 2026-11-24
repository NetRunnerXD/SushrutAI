"""Chat intent routing: no diagnosis for greetings, food or medicine questions."""
import json

import pytest

from app.llm import orchestrator
from app.llm.intent import _phrase_intent, keyword_intent
from app.llm.providers import LlmResult


@pytest.mark.parametrize("text,expected", [
    ("Hi", "greeting"), ("hello!", "greeting"), ("Namaste ji", "greeting"), ("hi, how are you?", "greeting"),
    ("नमस्ते", "greeting"), ("வணக்கம்", "greeting"), ("thanks", "thanks_bye"), ("ok thank you", "thanks_bye"),
    ("धन्यवाद", "thanks_bye"), ("bye", "thanks_bye"), ("who are you", "about_app"), ("आप कौन हैं", "about_app"),
    ("what can you do", "about_app"),
])
def test_conversational_phrases(text, expected):
    assert _phrase_intent(text) == expected


@pytest.mark.parametrize("text", ["hi I have a headache", "hello doctor, my child has fever", "thanks, but my pain is worse"])
def test_conversational_words_with_a_real_question_are_not_small_talk(text):
    assert _phrase_intent(text) is None
    assert keyword_intent(text) == "symptoms"


@pytest.mark.parametrize("text,expected", [
    ("What should I eat for lunch", "nutrition_lifestyle"),
    ("what to eat in fever", "nutrition_lifestyle"),          # asks about food: no diagnosis list
    ("kya khana chahiye diabetes me", "nutrition_lifestyle"),
    ("is it safe to walk after meals", "nutrition_lifestyle"),
    ("I have fever since 2 days", "symptoms"),
    ("fever for 2 days, took crocin", "symptoms"),           # a medicine mentioned while describing symptoms
    ("I can't eat, stomach pain and vomiting", "symptoms"),  # 'eat' as a symptom, not a food question
    ("I am having difficulty swallowing food since yesterday due to soreness in my throat what could be the reason?", "symptoms"),
    ("I can't walk, knee pain", "symptoms"),
    ("pet me dard hai", "symptoms"), ("mujhe bukhar hai", "symptoms"), ("सिर दर्द और उल्टी", "symptoms"),
    ("can I take dolo 650 for fever", "medicine"), ("What is Dolo 650", "medicine"),
    ("side effects of metformin", "medicine"),
    ("explain my CBC report", "report_question"), ("help me understand my report", "report_question"),
    ("my sugar is 250", "report_question"),
])
def test_keyword_routing(text, expected):
    assert keyword_intent(text) == expected


@pytest.mark.parametrize("text", ["standard test", "kitchen tips", "testing the pandemic numbers", "multi grain atta"])
def test_symptom_words_inside_other_words_do_not_count(text):
    assert keyword_intent(text) != "symptoms"


def _events(body: str) -> dict:
    out = {}
    for block in body.split("\n\n"):
        if block.startswith("event:"):
            name = block.split("\n")[0][7:]
            out[name] = json.loads(block.split("data: ", 1)[1])
    return out


async def test_greeting_needs_no_model_and_shows_no_diagnosis(client, guest):
    # The test environment has no model server: a successful answer proves no model was called.
    sid = (await client.post("/chat/sessions", json={"lang": "en"}, headers=guest)).json()["id"]
    r = await client.post(f"/chat/sessions/{sid}/messages", json={"text": "Hi"}, headers=guest)
    ev = _events(r.text)
    assert "error" not in ev
    s = ev["answer"]["structured"]
    assert s["kind"] == "greeting" and "possible_conditions" not in s and "suggested_tests" not in s
    assert {a["href"] for a in s["actions"]} >= {"/ask/check", "/medicines/rx"}


async def test_non_symptom_answer_is_stripped_of_diagnosis(client, guest, monkeypatch):
    """Even if the model returns a diagnosis for a lunch question, it never reaches the user."""
    async def fake_chat(messages, schema=None, **kw):
        assert "possible_conditions" not in json.dumps(schema)  # the general schema was used
        assert "Do not give a list of diseases or tests" in messages[0]["content"]
        return LlmResult(text=json.dumps({
            "reply": "A plate of dal, roti and sabzi with curd is a balanced lunch.",
            "tips": ["Add a bowl of salad", "Take an antibiotic"],  # medicine advice must be removed
            "follow_up_questions": [], "possible_conditions": [{"name": "Diabetes"}],
            "suggested_tests": ["HbA1c"], "specialist": "Endocrinologist"}),
            provider="test", model="test", tier="L", latency_ms=1)

    async def safe(text, lang="en"):
        from app.safety.guard import GuardResult
        return GuardResult(unsafe=False, categories=[], message="", call_numbers=[])

    monkeypatch.setattr(orchestrator, "clinical_chat", fake_chat)
    monkeypatch.setattr(orchestrator.guard, "check_user_message", safe)
    sid = (await client.post("/chat/sessions", json={"lang": "en"}, headers=guest)).json()["id"]
    r = await client.post(f"/chat/sessions/{sid}/messages", json={"text": "What should I eat for lunch?"}, headers=guest)
    s = _events(r.text)["answer"]["structured"]
    assert s["kind"] == "nutrition_lifestyle" and s["suggest_check"] is False
    assert not {"possible_conditions", "suggested_tests", "specialist"} & s.keys()
    assert "Take an antibiotic" not in s["tips"] and "Add a bowl of salad" in s["tips"]


async def test_attachment_only_message_is_accepted(client, guest):
    sid = (await client.post("/chat/sessions", json={"lang": "en"}, headers=guest)).json()["id"]
    r = await client.post(f"/chat/sessions/{sid}/messages", json={"text": ""}, headers=guest)
    assert r.status_code == 422  # neither text nor photo
