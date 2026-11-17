import pytest

from app.safety import red_flags
from app.security.pii import scrub


@pytest.mark.parametrize("text,rule", [
    ("I have chest pain spreading to my left arm", "cardiac_chest_pain"),
    ("सीने में दर्द हो रहा है और पसीना आ रहा है", "cardiac_chest_pain"),
    ("seene me dard ho raha hai", "cardiac_chest_pain"),
    ("Papa ka muh tedha ho gaya aur bolne mein dikkat", "stroke_fast"),
    ("papa ka face ek side latak gaya hai aur bolne me dikkat ho rahi hai", "stroke_fast"),
    ("mummy ka ek side sunn ho gaya", "stroke_fast"),
    ("पापा का चेहरा एक तरफ लटक गया है", "stroke_fast"),
    ("I can't breathe properly", "breathing"),
    ("saans lene me bahut dikkat hai", "breathing"),
    ("my child swallowed kerosene", "poisoning_overdose"),
    ("saap ne kaat liya", "poisoning_overdose"),
    ("fever for 3 days and neck stiffness", "meningitis_signs"),
    ("8 months pregnant and bleeding", "pregnancy_emergency"),
    ("বুকে ব্যথা করছে", "cardiac_chest_pain"),
    ("நெஞ்சு வலி", "cardiac_chest_pain"),
])
def test_emergencies_detected(text, rule):
    r = red_flags.check(text)
    assert r.triggered and rule in r.rules
    assert "108" in r.call_numbers


@pytest.mark.parametrize("text", ["I want to kill myself", "मैं आत्महत्या करना चाहता हूँ", "jeena nahi chahta"])
def test_self_harm_routes_to_tele_manas(text):
    r = red_flags.check(text)
    assert r.kind == "mental_health"
    assert "14416" in r.call_numbers


@pytest.mark.parametrize("text", [
    "I have a mild headache since morning",
    "I am fit and healthy, what diet is good for me?",
    "Is Dolo 650 safe with Pan 40?",
    "mujhe halka zukam hai",
])
def test_benign_not_flagged(text):
    assert not red_flags.check(text).triggered


def test_infant_fever_uses_profile_age():
    assert red_flags.check("baby has fever", age_years=0.1).triggered
    assert not red_flags.check("baby has fever", age_years=2).triggered


def test_hindi_message_when_lang_hi():
    r = red_flags.check("chest pain", lang="hi")
    assert "108" in r.messages[0] and "सीने" in r.messages[0]


def test_pii_scrub_indian_identifiers():
    text = ("My name is Ramesh Kumar, Aadhaar 2345 6789 0123, ABHA 91-1234-5678-9012, "
            "phone +91 98765 43210, email ramesh@example.com, PAN ABCDE1234F")
    out, counts = scrub(text)
    for leaked in ("Ramesh", "2345 6789 0123", "91-1234-5678-9012", "98765 43210", "ramesh@example.com", "ABCDE1234F"):
        assert leaked not in out
    assert {"NAME", "AADHAAR", "ABHA", "PHONE", "EMAIL", "PAN"} <= counts.keys()


def test_pii_keeps_clinical_numbers():
    out, _ = scrub("BP 140/90, sugar 180 mg/dL, Dolo 650 twice daily for 5 days")
    assert out == "BP 140/90, sugar 180 mg/dL, Dolo 650 twice daily for 5 days"


def test_self_care_medicine_advice_is_detected():
    from app.llm.orchestrator import _MED_ADVICE
    assert _MED_ADVICE.search("Take Paracetamol (Dolo 650) as needed")
    assert _MED_ADVICE.search("बुखार के लिए गोली लें")
    assert not _MED_ADVICE.search("Drink plenty of fluids and rest")


def test_urgency_floor_only_raises():
    from app.llm.orchestrator import URGENCY_FLOOR, _raise
    assert _raise("self_care", "see_doctor_24h") == "see_doctor_24h"
    assert _raise("emergency", "see_doctor_24h") == "emergency"
    floors = {f for pat, f in URGENCY_FLOOR if pat.search("Dengue fever")}
    assert floors == {"see_doctor_24h"}
    assert not any(pat.search("Common cold") for pat, _ in URGENCY_FLOOR)


def test_llama_guard_output_parsing_ignores_specialized_advice():
    from app.safety.guard import IGNORED, parse_guard
    assert parse_guard("safe") == (False, [])
    assert parse_guard("unsafe\nS6") == (True, ["S6"]) and "S6" in IGNORED  # normal health questions get S6
    assert parse_guard("unsafe\nS11") == (True, ["S11"])
    assert parse_guard("unsafe\nS2,S9") == (True, ["S2", "S9"])


def test_broken_model_json_never_reaches_user_raw():
    from app.llm.orchestrator import _reply_from_broken_json
    out = _reply_from_broken_json('{\n  "reply": "I am not a doctor and cannot determine if you are HIV positive. Get tested at an ICTC centre", "possi')
    assert not out.lstrip().startswith("{") and "cannot determine" in out and "cut short" in out
    assert not _reply_from_broken_json('{"possible_conditions": [').startswith("{")


def test_dedupe_runaway_repetition():
    from app.llm.orchestrator import _reply_from_broken_json, dedupe_repeats

    loop = "Rest and drink fluids. " + "I am not sure about the cause of your symptoms. " * 12
    text, changed = dedupe_repeats(loop)
    assert changed and text.count("not sure about the cause") == 1
    # A looping answer that hit the token cap is complete once de-duplicated: no "cut short" notice.
    broken = '{"reply": "' + loop
    assert "cut short" not in _reply_from_broken_json(broken)
    assert dedupe_repeats("Short. Short.") == ("Short. Short.", False)  # tiny sentences are left alone


@pytest.mark.parametrize("text", [
    "a stray dog bit my son on the hand",
    "गली के कुत्ते ने मेरे बेटे के हाथ पर काट लिया, थोड़ा खून आ रहा है",
    "kutte ne kaat liya",
    "I was scratched by a cat yesterday",
])
def test_animal_bite_flags_rabies(text):
    r = red_flags.check(text)
    assert r.triggered and "animal_bite_rabies" in r.rules and r.kind == "urgent"


@pytest.mark.parametrize("text", ["my throat hurts and bolne me dikkat", "I have a hot dog for lunch"])
def test_new_rules_do_not_fire_on_lookalikes(text):
    assert not red_flags.check(text).triggered
