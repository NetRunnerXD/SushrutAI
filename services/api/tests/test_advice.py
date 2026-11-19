"""Advice, not refusals: refusal detection, medicine-name filter, source attribution, KB page parsers."""
import asyncio
import re

from app.llm import orchestrator as o
from app.pipelines import kb
from app.safety.disclaimer import PROHIBITIONS


def test_rules_ask_for_help_not_refusal():
    rules = " ".join(PROHIBITIONS)
    assert "say you are not sure" not in rules
    assert "never name a medicine" in rules.lower()
    assert "can be seen with" in rules
    assert "Never refuse" in rules


def test_refusal_detected():
    assert o._is_refusal({"reply": "I cannot provide medical advice. Please consult a doctor.", "self_care": []})
    assert o._is_refusal({"reply": "Fever can have many causes.", "self_care": ["Please consult a doctor."]})
    assert not o._is_refusal({"reply": "Your symptoms can be seen with a viral fever.",
                              "self_care": ["Rest and drink plenty of fluids", "See a doctor if it lasts 3 days"]})


def test_refusal_sentence_stripped():
    s = {"reply": "I'm not a doctor, but this can be linked to acidity. Eat smaller meals."}
    assert o._strip_refusals(s)
    assert "not a doctor" not in s["reply"] and "acidity" in s["reply"]


def _names(monkeypatch, names):
    rx = re.compile(r"(?i)\b(" + "|".join(names) + r")\b")

    async def fake(db):
        return rx
    monkeypatch.setattr(o, "_medicine_names", fake)


def test_medicine_names_removed(monkeypatch):
    _names(monkeypatch, ["paracetamol", "omeprazole", "dolo"])
    s = {"reply": "This can be acidity. Omeprazole may help. Eat smaller meals.",
         "self_care": ["Avoid spicy food", "Take paracetamol for pain"]}
    w = asyncio.run(o._strip_medicine_names(None, s, "burning in chest after meals", "en"))
    assert w == ["removed_medicine_name"]
    assert "Omeprazole" not in s["reply"] and "smaller meals" in s["reply"]
    assert not any("paracetamol" in x for x in s["self_care"])
    assert o.MED_REFERRAL["en"] in s["self_care"]


def test_medicine_asked_about_can_be_discussed(monkeypatch):
    _names(monkeypatch, ["paracetamol", "dolo"])
    s = {"reply": "Dolo contains paracetamol; it is usually safe with BP tablets.", "tips": []}
    assert asyncio.run(o._strip_medicine_names(None, s, "is Dolo safe with my BP tablets?", "en")) == []
    assert "paracetamol" in s["reply"]


def test_schema_has_warning_signs():
    assert "watch_for" in o.ANSWER_SCHEMA["required"]
    assert "watch_for" in o.GENERAL_SCHEMA["properties"]


def test_page_text_main_only():
    html = """<html><head><title>Dengue | CDC</title></head><body><nav><li>Menu item</li></nav>
    <main><h1>About Dengue</h1><p>Dengue is spread by mosquito bites.</p><h2>Symptoms</h2>
    <ul><li>Fever</li><li>Rash</li></ul><div class="share-links"><p>Share this</p></div></main>
    <footer><p>Footer</p></footer></body></html>"""
    title, text = kb.page_text(html)
    assert title == "About Dengue"
    assert "mosquito" in text and "- Fever" in text and "## Symptoms" in text
    assert "Menu item" not in text and "Footer" not in text and "Share this" not in text


def test_medlineplus_xml_english_only():
    xml = """<health-topics><health-topic title="Asthma" url="https://medlineplus.gov/asthma.html" language="English">
    <also-called>Bronchial asthma</also-called><full-summary>&lt;p&gt;Asthma is a lung disease.&lt;/p&gt;</full-summary>
    </health-topic><health-topic title="Asma" url="https://medlineplus.gov/spanish/asthma.html" language="Spanish">
    <full-summary>x</full-summary></health-topic></health-topics>"""
    t = kb.medlineplus_topics(xml)
    assert len(t) == 1 and t[0]["title"] == "Asthma"
    assert "lung disease" in t[0]["text"] and "Bronchial asthma" in t[0]["text"]


def test_cdc_links_skip_clinician_pages():
    html = ('<a href="/dengue/signs-symptoms/index.html"></a><a href="/dengue/hcp/clinical-care/index.html"></a>'
            '<a href="/dengue/prevention/index.html"></a><a href="/dengue/es/index.html"></a>')
    assert kb.cdc_links("dengue", html) == ["/dengue/prevention/index.html", "/dengue/signs-symptoms/index.html"]


def test_grounding_adds_no_note():
    from app.rag.grounding import NOTE, ground
    out, _ = ground("Drink ORS 500 ml every hour. Rest well.", [], "en")
    assert NOTE["en"] not in out


def test_consulted_thresholds_from_eval():
    assert o.CONSULT_TOP_MIN < o.CONSULT_MIN <= 0.3


def test_hinglish_is_detected_for_translation():
    assert o.is_hinglish("subah se sar dard ho raha hai, kya karu?")
    assert o.is_hinglish("pet mein gas aur bloating rehti hai, kya karu?")
    assert not o.is_hinglish("My knee hurts when I climb stairs. What could it be?")
    assert not o.is_hinglish("Is it safe to take Dolo 650 with BP tablets?")


def test_followups_search_with_the_previous_topic():
    hist = [{"role": "user", "content": "I have dengue fever"}, {"role": "assistant", "content": "Dengue can ..."}]
    assert o.retrieval_query("How long will it last?", hist) == "I have dengue fever How long will it last?"
    assert o.retrieval_query("What foods are good for strong bones in children?", hist).startswith("What foods")
    assert o.retrieval_query("How long will it last?", None) == "How long will it last?"


def test_long_chat_is_folded_into_a_note():
    hist = []
    for i in range(6):
        hist += [{"role": "user", "content": f"question {i}"},
                 {"role": "assistant", "content": f"Answer {i}. More detail here.",
                  "structured": {"reply": f"Answer {i}. More.", "possible_conditions": [{"name": "Flu", "name_en": "Flu"}]}}]
    msgs = o.conversation_messages(hist)
    assert msgs[0]["content"].startswith("CONVERSATION SO FAR") and "question 0" in msgs[0]["content"]
    assert "[possible: Flu]" in msgs[0]["content"]
    assert [m["content"] for m in msgs if m["role"] == "user"][-1] == "question 5"
    assert len(msgs) == 2 + 6
    assert o.conversation_messages(hist[:4]) and len(o.conversation_messages(hist[:4])) == 4


def test_followup_in_symptom_chat_keeps_symptom_kind():
    hist = [{"role": "user", "content": "high fever 2 days"},
            {"role": "assistant", "content": "Could be dengue.", "structured": {"kind": "symptoms"}}]
    assert o.followup_kind("How long will it last?", hist) == "symptoms"
    assert o.followup_kind("What foods are good for strong bones in growing children?", hist) is None
    assert o.followup_kind("How long will it last?", None) is None
