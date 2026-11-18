from app.llm.orchestrator import MED_REFERRAL, _strip_doses


def test_dose_sentences_and_tips_removed_for_any_kind():
    s = {"reply": "Rest well. Adults can take 500mg to 1 gram every 4 to 6 hours. Drink fluids.",
         "self_care": ["Do not exceed 4000 mg in 24 hours", "Drink plenty of water"]}
    assert _strip_doses(s, "symptoms", "en") == ["removed_dose_amount"]
    assert s["reply"] == "Rest well. Drink fluids."
    assert s["self_care"] == ["Drink plenty of water", MED_REFERRAL["en"]]


def test_lab_units_and_food_amounts_kept():
    s = {"reply": "Your fasting sugar of 110 mg/dL is a little high.", "tips": ["Eat 30 g of fibre a day", "Drink 200 ml ORS"]}
    assert _strip_doses(s, "report_question", "en") == []
    assert s["tips"] == ["Eat 30 g of fibre a day", "Drink 200 ml ORS"]


def test_ml_counts_in_medicine_answers():
    s = {"reply": "Ask your doctor.", "tips": ["Give 5 ml syrup twice a day"]}
    assert _strip_doses(s, "medicine", "en") == ["removed_dose_amount"]
    assert s["tips"] == [MED_REFERRAL["en"]]


def test_insulin_units_removed():
    from app.llm.orchestrator import _strip_doses
    s = {"reply": "A common starting point is 0.15-0.20 units/kg per injection. Check your sugar regularly.", "tips": []}
    _strip_doses(s, "general_health", "en")
    assert "units" not in s["reply"] and "Check your sugar" in s["reply"]
