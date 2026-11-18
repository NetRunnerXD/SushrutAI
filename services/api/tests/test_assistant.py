"""Voice actions: parser (English / Hindi / Hinglish) and the /assistant/parse endpoint."""
import pytest

from app.assistant.actions import parse

MEDS = [{"id": "1", "name": "Pan 40"}, {"id": "2", "name": "Metformin 500"}, {"id": "3", "name": "Telma 40"}]
FAM = [{"id": "k", "name": "Asha", "relation": "child"}]


@pytest.mark.parametrize("text,readings", [
    ("my BP is 130 over 85", {"bp_sys": 130, "bp_dia": 85}),
    ("130/85", {"bp_sys": 130, "bp_dia": 85}),
    ("BP 130 by 85", {"bp_sys": 130, "bp_dia": 85}),
    ("बीपी 130 बटा 85", {"bp_sys": 130, "bp_dia": 85}),
    ("sugar 110 fasting", {"glucose_f": 110}),
    ("khali pet sugar 104", {"glucose_f": 104}),
    ("sugar after lunch 180", {"glucose_pp": 180}),
    ("sugar 145", {"glucose_r": 145}),
    ("weight 72 kilo", {"weight": 72}),
    ("mera wajan 68 kg hai", {"weight": 68}),
    ("pulse 80", {"hr": 80}),
    ("oxygen 96", {"spo2": 96}),
    ("temperature 101", {"temp": 38.3}),       # Fahrenheit converted to °C
    ("temperature 37.5", {"temp": 37.5}),
    ("my weight is 72 kg and bp 120/80", {"bp_sys": 120, "bp_dia": 80, "weight": 72}),
])
def test_log_vital(text, readings):
    a = parse(text, meds=MEDS, family=FAM)
    assert a and a.action == "log_vital" and a.confirm
    assert {r["type"]: r["value"] for r in a.params["readings"]} == readings


@pytest.mark.parametrize("text", [
    "is 130/85 normal?", "what should my sugar be?", "kya 140 sugar theek hai", "fever 101 since yesterday",
    "BP 150/95 and I have a headache", "I have fever since 2 days", "what is dolo 650", "hello", "BP 90 by 130",
    "bp 400/90",
])
def test_not_an_action(text):
    assert parse(text, meds=MEDS, family=FAM) is None  # questions and symptoms go to the assistant


@pytest.mark.parametrize("text,ids,slot", [
    ("I took Pan 40", {"1"}, None), ("mark metformin taken", {"2"}, None), ("maine telma le li", {"3"}, None),
    ("took my night medicines", {"1", "2", "3"}, "night"), ("सुबह की दवा ले ली", {"1", "2", "3"}, "morning"),
])
def test_log_dose(text, ids, slot):
    a = parse(text, meds=MEDS, family=FAM)
    assert a and a.action == "log_dose" and a.confirm
    assert {m["id"] for m in a.params["medicines"]} == ids and a.params["slot"] == slot


@pytest.mark.parametrize("text,action,check", [
    ("did I take my morning tablet?", "dose_status", {"slot": "morning"}),
    ("kya maine subah ki dawai li", "dose_status", {"slot": "morning"}),
    ("open my reports", "navigate", {"href": "/records?tab=labs"}),
    ("reports kholo", "navigate", {"href": "/records?tab=labs"}),
    ("show my prescriptions", "navigate", {"href": "/medicines/rx"}),
    ("go to family", "navigate", {"href": "/me/family"}),
    ("open vaccines", "navigate", {"href": "/me/care"}),
    ("show my bp chart", "navigate", {"href": "/records?tab=vitals"}),
    ("switch to Asha", "switch_profile", {"id": "k"}),
    ("Asha ka record kholo", "switch_profile", {"id": "k"}),
    ("switch to me", "switch_profile", {"id": None}),
    ("read my last report", "read_latest", {}),
    ("meri pichli report sunao", "read_latest", {}),
])
def test_read_only_actions(text, action, check):
    a = parse(text, meds=MEDS, family=FAM)
    assert a and a.action == action and not a.confirm
    assert {k: a.params.get(k) for k in check} == check


async def test_parse_endpoint_proposes_but_never_writes(client, guest):
    r = (await client.post("/assistant/parse", json={"text": "my BP is 130 over 85"}, headers=guest)).json()
    assert r["action"] == "log_vital" and r["confirm"] and r["say"] == "Save BP 130/85 mmHg?"
    assert (await client.get("/me/vitals", headers=guest)).json() == []  # nothing saved until the person confirms
    assert (await client.post("/assistant/parse", json={"text": "is 130/85 normal?"}, headers=guest)).json() == {"action": None}


async def test_dose_actions(client, guest):
    m = (await client.post("/me/medications", json={"name": "Pan 40", "timing": "before_food",
         "frequency": {"morning": 1, "afternoon": 0, "night": 1, "per_day": 2}}, headers=guest)).json()
    r = (await client.post("/assistant/parse", json={"text": "I took my night Pan 40"}, headers=guest)).json()
    assert r["action"] == "log_dose" and r["params"]["items"] == [{"medication_id": m["id"], "name": "Pan 40", "slot": "night"}]
    s = (await client.post("/assistant/parse", json={"text": "did I take my night tablets?"}, headers=guest)).json()
    assert s["action"] == "dose_status" and s["params"]["left"] == ["Pan 40"]
    await client.post("/me/doses", json={"medication_id": m["id"], "slot": "night", "status": "taken"}, headers=guest)
    s = (await client.post("/assistant/parse", json={"text": "did I take my night tablets?"}, headers=guest)).json()
    assert s["params"]["taken"] == ["Pan 40"] and "All done" in s["say"]
    hi = (await client.post("/assistant/parse", json={"text": "kya maine raat ki dawai li", "lang": "hi"}, headers=guest)).json()
    assert "सब ले ली गई" in hi["say"]


async def test_switch_profile_and_member_wording(client, guest):
    kid = (await client.post("/family", json={"name": "Asha", "relation": "child"}, headers=guest)).json()["id"]
    r = (await client.post("/assistant/parse", json={"text": "switch to Asha"}, headers=guest)).json()
    assert r["action"] == "switch_profile" and r["params"]["id"] == kid
    r = (await client.post("/assistant/parse", json={"text": "weight 18 kg"}, headers={**guest, "X-Profile-Id": kid})).json()
    assert r["say"] == "Save weight 18 kg for Asha?"
