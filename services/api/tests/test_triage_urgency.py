"""Symptom-check urgency: deterministic labels, probability-aware recommendation, named-list likelihoods."""
import pytest

from app.triage import engine
from app.triage.urgency import condition_urgency, recommend


@pytest.mark.parametrize("name,expected", [
    ("Common Cold", "self_care"), ("Tension Headache", "self_care"), ("Viral Gastroenteritis", "self_care"),
    ("Acute Bronchitis", "see_doctor_week"), ("Migraine", "see_doctor_week"), ("Some Rare Syndrome", "see_doctor_week"),
    ("Pneumonia", "see_doctor_24h"), ("Dengue Fever", "see_doctor_24h"), ("Pulmonary tuberculosis", "see_doctor_24h"),
    ("Asthma Exacerbation", "see_doctor_24h"), ("Cholera", "see_doctor_24h"),
    ("Meningitis", "emergency"), ("Subarachnoid Hemorrhage", "emergency"), ("Myocardial infarction", "emergency"),
])
def test_condition_urgency_table(name, expected):
    assert condition_urgency(name) == expected


def test_high_risk_people_are_seen_sooner():
    assert condition_urgency("Influenza", high_risk=True) == "see_doctor_24h"
    assert condition_urgency("Common Cold", high_risk=True) == "see_doctor_week"
    assert condition_urgency("Meningitis", high_risk=True) == "emergency"  # never lowered


def _r(*pairs):
    return [{"urgency": u, "probability": p} for u, p in pairs]


def test_recommendation_follows_probability():
    assert recommend(_r(("self_care", 0.7), ("see_doctor_24h", 0.05)), red_flag_yes=False) == "self_care"
    assert recommend(_r(("self_care", 0.6), ("see_doctor_24h", 0.10)), red_flag_yes=False) == "see_doctor_week"
    assert recommend(_r(("self_care", 0.5), ("see_doctor_24h", 0.30)), red_flag_yes=False) == "see_doctor_24h"
    assert recommend(_r(("see_doctor_week", 0.8), ("emergency", 0.10)), red_flag_yes=False) == "see_doctor_24h"
    assert recommend(_r(("see_doctor_week", 0.6), ("emergency", 0.30)), red_flag_yes=False) == "emergency"


def test_danger_sign_answer_always_means_today():
    assert recommend(_r(("self_care", 0.95)), red_flag_yes=True) == "see_doctor_24h"


def test_replay_cough_check_from_browser():
    """The real plan behind the over-cautious result: the model labelled every condition urgent and wrote a
    position-template likelihood table. All answers were 'no'."""
    plan = {"conditions": [{"name": n, "prior": p, "urgency": "emergency", "specialist": "General Physician"}
                           for n, p in [("Common Cold", .3), ("Influenza", .2), ("Bronchitis", .2), ("Pneumonia", .1),
                                        ("COVID-19", .1), ("Asthma Exacerbation", .1)]],
            "findings": [{"id": f"f{i}", "question": q, "red_flag": red, "topic": "general", "likelihoods": lk}
                         for i, (q, red, lk) in enumerate([
                             ("Fever?", False, [.4, .3, .2, .1, .05, .01]),
                             ("Short of breath?", True, [.3, .2, .1, .05, .02, .01]),
                             ("Chest pain?", True, [.2, .1, .05, .02, .01, .01]),
                             ("Phlegm?", False, [.5, .4, .3, .2, .1, .01])])]}
    st = engine.init_state(plan, sex="male", age=34)
    assert [c["urgency"] for c in st["conditions"]] == ["self_care", "self_care", "see_doctor_week", "see_doctor_24h",
                                                        "self_care", "see_doctor_24h"]
    for f in st["findings"]:
        engine.update(st, f["id"], "no")
    rec = recommend(engine.summary(st), red_flag_yes=bool(engine.red_flag_answered(st)))
    assert rec in ("self_care", "see_doctor_week")  # was "see_doctor_24h" (see a doctor today)


def test_named_lists_become_likelihoods():
    names = ["Common Cold", "Pneumonia", "Asthma Exacerbation"]
    row = engine.likelihood_row({"common_in": ["pneumonia", "Asthma"], "rare_in": ["common cold"]}, names)
    assert row == [engine.LK_RARE, engine.LK_COMMON, engine.LK_COMMON]
    assert engine.likelihood_row({"common_in": ["Something else"], "rare_in": []}, names) is None  # no signal: dropped


def test_new_plan_format_end_to_end():
    plan = {"conditions": [{"name": "Tension headache", "prior": .6, "specialist": "General Physician"},
                           {"name": "Migraine", "prior": .3, "specialist": "Neurologist"},
                           {"name": "Meningitis", "prior": .1, "specialist": "Neurologist"}],
            "findings": [
                {"id": "neck", "question": "Stiff neck?", "red_flag": True, "topic": "general",
                 "common_in": ["Meningitis"], "rare_in": ["Tension headache", "Migraine"]},
                {"id": "light", "question": "Does light hurt your eyes?", "red_flag": False, "topic": "general",
                 "common_in": ["Migraine", "Meningitis"], "rare_in": ["Tension headache"]},
                {"id": "band", "question": "Feels like a tight band?", "red_flag": False, "topic": "general",
                 "common_in": ["Tension headache"], "rare_in": ["Meningitis"]}]}
    st = engine.init_state(plan, sex="female", age=30)
    assert engine.next_question(st)["id"] == "neck"  # danger signs first
    engine.update(st, "neck", "no"); engine.update(st, "light", "no"); engine.update(st, "band", "yes")
    ranked = engine.summary(st)
    assert ranked[0]["name"] == "Tension headache"
    assert recommend(ranked, red_flag_yes=False) == "self_care"

    st = engine.init_state(plan, sex="female", age=30)
    engine.update(st, "neck", "yes")
    assert recommend(engine.summary(st), red_flag_yes=bool(engine.red_flag_answered(st))) in ("see_doctor_24h", "emergency")


def test_template_tables_are_detected():
    assert engine.is_template_table([[.4, .3, .2, .1, .05, .01], [.3, .2, .1, .05, .02, .01], [.5, .4, .3, .2, .1, .01]])
    assert not engine.is_template_table([[.1, .8, .2, .7], [.9, .1, .1, .6], [.2, .2, .9, .1]])
    assert not engine.is_template_table([[.7, .1, .1], [.3, .3, .9]])  # 3 conditions: too few to judge


def test_specialist_follows_the_condition_that_sets_urgency():
    from app.triage.urgency import recommended_specialist
    ranked = [{"urgency": "see_doctor_week", "probability": .5, "specialist": "ENT Specialist"},
              {"urgency": "emergency", "probability": .22, "specialist": "Neurologist"}]
    assert recommended_specialist(ranked, "see_doctor_24h") == "General Physician"  # not ENT for possible meningitis
    assert recommended_specialist(ranked, "see_doctor_week") == "ENT Specialist"
    assert recommended_specialist(ranked, "self_care") is None
