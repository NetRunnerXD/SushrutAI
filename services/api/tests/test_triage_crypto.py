import pytest
from cryptography.exceptions import InvalidTag

from app.security.crypto import Cipher, new_wrapped_dek
from app.triage import engine

LLM_PLAN = {
    "conditions": [
        {"name": "Dengue", "prior": 0.4, "urgency": "see_doctor_24h", "specialist": "General Physician"},
        {"name": "Viral fever", "prior": 0.4, "urgency": "self_care", "specialist": "General Physician"},
        {"name": "Malaria", "prior": 0.2, "urgency": "see_doctor_24h", "specialist": "General Physician"},
    ],
    "findings": [
        {"id": "retro_orbital", "question": "Pain behind the eyes?", "red_flag": False, "topic": "general",
         "likelihoods": [0.7, 0.1, 0.1]},
        {"id": "chills", "question": "Shivering with chills?", "red_flag": False, "topic": "general",
         "likelihoods": [0.3, 0.3, 0.9]},
        {"id": "bleeding", "question": "Bleeding gums?", "red_flag": True, "topic": "general",
         "likelihoods": [0.2, 0.01, 0.02]},
        {"id": "preg", "question": "Could you be pregnant?", "red_flag": False, "topic": "pregnancy",
         "likelihoods": [0.1, 0.1, 0.1]},
    ],
}


def test_pregnancy_question_filtered_for_men():
    st = engine.init_state(LLM_PLAN, sex="male", age=30)
    assert "preg" not in {f["id"] for f in st["findings"]}


def test_red_flag_question_asked_first():
    st = engine.init_state(LLM_PLAN, sex="female", age=30)
    assert engine.next_question(st)["id"] == "bleeding"


def test_bayes_update_and_information_gain():
    st = engine.init_state(LLM_PLAN, sex="female", age=30)
    engine.update(st, "bleeding", "no")
    q = engine.next_question(st)
    assert q["id"] in ("retro_orbital", "chills")
    engine.update(st, "retro_orbital", "yes")
    ranked = engine.summary(st)
    assert ranked[0]["name"] == "Dengue"
    assert abs(sum(st["posterior"]) - 1) < 1e-9


def test_unsure_does_not_change_beliefs():
    st = engine.init_state(LLM_PLAN, sex="female", age=30)
    before = list(st["posterior"])
    engine.update(st, "chills", "unsure")
    assert st["posterior"] == before


def test_eig_zero_for_uninformative_question():
    assert engine.expected_information_gain([0.5, 0.5], [0.3, 0.3]) == pytest.approx(0.0, abs=1e-12)


def test_cipher_roundtrip_and_binding():
    dek = new_wrapped_dek()
    c = Cipher(dek, "user-1")
    blob = c.enc("Allergy: penicillin")
    assert c.dec(blob) == "Allergy: penicillin"
    with pytest.raises(InvalidTag):
        Cipher(dek, "user-2").dec(blob)  # ciphertext is bound to its owner


def test_crypto_shredding():
    blob = Cipher(new_wrapped_dek(), "u").enc("secret")
    with pytest.raises(InvalidTag):
        Cipher(new_wrapped_dek(), "u").dec(blob)  # without the original DEK the data is unrecoverable


def test_truncated_model_json_is_salvaged():
    from app.llm.providers import parse_json_loose
    out = parse_json_loose('{"medications": [{"name": "Dolo 650", "confidence": 0.9}, {"name": "Pan 4')
    assert out["_truncated"] is True and out["medications"][0]["name"] == "Dolo 650"


def test_must_consider_public_health_rules():
    assert engine.must_consider("fever with chills on alternate days", "MH", 1) == ["Malaria"]
    assert engine.must_consider("high fever and body ache", "KA", 8) == ["Dengue", "Malaria", "Typhoid (enteric fever)"]
    assert "Malaria" in engine.must_consider("बुखार है", "OR", 2)  # endemic state, any season
    assert engine.must_consider("cough for 3 weeks", None, 3) == ["Pulmonary tuberculosis"]
    assert engine.must_consider("cough since yesterday", None, 3) == []


def test_missing_required_detection():
    plan = {"conditions": [{"name": "Dengue fever"}, {"name": "Viral fever"}]}
    assert engine.missing_required(plan, ["Dengue", "Malaria"]) == ["Malaria"]
