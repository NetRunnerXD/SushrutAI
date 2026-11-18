import json
from datetime import date

import pytest

from app.clinical.labs import parse_lab_line, parse_lab_report
from app.routers.records import build_ics, reminder_slots
from app.speech.models import is_native_script, normalise_for_tts


@pytest.mark.parametrize("line,loinc,value,flag", [
    ("Haemoglobin   10.2   g/dL   12.0 - 15.0", "718-7", 10.2, "L"),
    ("Platelet Count : 98000 /cumm (150000 - 450000)", "777-3", 98000, "L"),
    ("HbA1c : 7.8 % (4.0-5.6)", "4548-4", 7.8, "H"),
    ("SGPT (ALT) 56 U/L 0-40", "1742-6", 56, "H"),
    ("Serum Creatinine 0.9 mg/dL 0.6 - 1.2", "2160-0", 0.9, "N"),
    ("LDL Cholesterol 162 mg/dL < 100", "13457-7", 162, "H"),
    ("HDL Cholesterol 38 mg/dL > 40", "2085-9", 38, "L"),
    ("Vitamin D (25-OH) 14.2 ng/mL 30 - 100", "1989-3", 14.2, "L"),
])
def test_lab_lines(line, loinc, value, flag):
    r = parse_lab_line(line)
    assert (r.loinc_code, r.value, r.flag) == (loinc, value, flag)


def test_lab_report_ignores_demographics():
    rows = parse_lab_report("Patient Name: Ramesh\nAge 45 Years\nTSH 6.8 uIU/mL 0.4 - 4.0\nSample collected on 12/03/2024")
    assert [r.loinc_code for r in rows] == ["3016-3"]


def test_reminder_slots_from_frequency():
    assert reminder_slots({"morning": 1, "afternoon": 0, "night": 1, "per_day": 2}) == ["morning", "night"]
    assert reminder_slots({"per_day": None, "label": "only when needed"}) == []
    assert reminder_slots(None) == []


def test_ics_calendar_is_valid_and_bounded():
    meds = [{"id": "m1", "name": "Pan 40", "frequency": {"morning": 1, "afternoon": 0, "night": 0, "per_day": 1},
             "timing": "before_food", "start_date": date(2026, 11, 20), "end_date": date(2026, 11, 25)}]
    ics = build_ics(meds, {"morning": "07:30", "afternoon": "14:00", "night": "21:00"}, date(2026, 11, 20))
    assert ics.startswith("BEGIN:VCALENDAR") and ics.rstrip().endswith("END:VCALENDAR")
    assert "DTSTART:20261120T073000" in ics and "RRULE:FREQ=DAILY;UNTIL=20261125T235959" in ics
    assert "SUMMARY:Take Pan 40 — before food" in ics and ics.count("BEGIN:VEVENT") == 1


def test_tts_normaliser_hindi():
    out = normalise_for_tts("650 mg दिन में दो बार", "hi")
    assert "छह पाँच शून्य" in out and "मिलीग्राम" in out and "mg" not in out


def test_native_script_detection():
    assert is_native_script("मुझे बुखार है") and not is_native_script("mujhe bukhar hai")


async def test_fhir_export_bundle(client, guest):
    await client.put("/me/profile", json={"dob": "1990-01-01", "sex": "female"}, headers=guest)
    await client.post("/me/allergies", json={"substance": "Penicillin", "severity": "severe", "reaction": "rash"}, headers=guest)
    await client.post("/me/vitals", json=[{"type": "bp_sys", "value": 128}], headers=guest)
    r = await client.get("/me/export/fhir", headers=guest)
    b = json.loads(r.text)
    types = sorted({e["resource"]["resourceType"] for e in b["entry"]})
    assert b["resourceType"] == "Bundle" and types == ["AllergyIntolerance", "Observation", "Patient"]
    obs = next(e["resource"] for e in b["entry"] if e["resource"]["resourceType"] == "Observation")
    assert obs["code"]["coding"][0]["code"] == "8480-6"  # LOINC systolic BP


async def test_offline_mode_blocks_cloud(client, guest):
    from app.db import SessionLocal, current_account_id, current_user_id
    from app.security.settings import cloud_allowed
    await client.post("/consents", json={"purposes": ["cloud_llm_deidentified"]}, headers=guest)
    me = (await client.get("/me/profile", headers=guest)).json()["user_id"]
    current_user_id.set(me)  # code outside a request must set the row-level-security scope itself
    current_account_id.set(me)
    async with SessionLocal() as db:
        assert await cloud_allowed(db, me, "cloud_llm_deidentified")
    await client.put("/me/settings", json={"offline_mode": True}, headers=guest)
    async with SessionLocal() as db:
        assert not await cloud_allowed(db, me, "cloud_llm_deidentified")


@pytest.mark.parametrize("line,loinc,lo,hi", [
    # real line formats from Indian lab reports (MedOCR-Vision eval misses)
    ("MONOCYTE 02 %", "5905-5", None, None),
    ("RDW 12.2 % N 11.5-14.5", "788-0", 11.5, 14.5),
    ("M C V 85 fl 80 - 100", "787-2", 80, 100),
    ("M.C.H.C. 33.3 g/dl 30-35", "786-4", 30, 35),
    ("Absolute Neutrophils Count 6072 /cumm 2000-7000", "751-8", 2000, 7000),
    ("Polymorphs 77 %", "770-8", None, None),
])
def test_real_indian_lab_formats(line, loinc, lo, hi):
    r = parse_lab_line(line)
    assert (r.loinc_code, r.ref_low, r.ref_high) == (loinc, lo, hi)


async def test_dose_tracker_and_emergency_card(client, guest):
    await client.put("/me/profile", json={"blood_group": "O+", "emergency_contact": "Asha 9876543210"}, headers=guest)
    await client.post("/me/allergies", json={"substance": "Penicillin"}, headers=guest)
    m = (await client.post("/me/medications", json={"name": "Pan 40", "timing": "before_food",
         "frequency": {"morning": 1, "afternoon": 0, "night": 0, "per_day": 1}}, headers=guest)).json()
    d = (await client.get("/me/doses", headers=guest)).json()
    assert d["progress"] == {"done": 0, "total": 1} and d["items"][0]["slot"] == "morning"
    await client.post("/me/doses", json={"medication_id": m["id"], "slot": "morning", "status": "taken"}, headers=guest)
    await client.post("/me/doses", json={"medication_id": m["id"], "slot": "morning", "status": "taken"}, headers=guest)  # idempotent
    d = (await client.get("/me/doses", headers=guest)).json()
    assert d["progress"] == {"done": 1, "total": 1} and d["items"][0]["status"] == "taken" and d["week"]["taken"] == 1
    card = (await client.get("/me/emergency-card", headers=guest)).json()
    assert card["blood_group"] == "O+" and card["allergies"] == ["Penicillin"] and card["medicines"] == ["Pan 40"]
