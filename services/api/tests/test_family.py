"""Family profiles and the doctor visit summary."""
from app.routers.visit import questions_to_ask


async def test_family_profiles_are_isolated(client, guest):
    # account holder's own data
    await client.post("/me/allergies", json={"substance": "Penicillin"}, headers=guest)

    r = await client.post("/family", json={"name": "Asha", "relation": "child", "dob": "2019-05-01", "sex": "female"},
                          headers=guest)
    assert r.status_code == 201, r.text
    kid = r.json()["id"]
    assert r.json()["age"] >= 6
    members = (await client.get("/family", headers=guest)).json()
    assert [(m["name"], m["relation"], m["sex"]) for m in members] == [("Asha", "child", "female")]

    as_kid = {**guest, "X-Profile-Id": kid}
    await client.post("/me/allergies", json={"substance": "Peanuts"}, headers=as_kid)
    await client.post("/me/medications", json={"name": "Calpol 250", "timing": "after_food"}, headers=as_kid)
    kid_allergies = [a["substance"] for a in (await client.get("/me/allergies", headers=as_kid)).json()]
    own_allergies = [a["substance"] for a in (await client.get("/me/allergies", headers=guest)).json()]
    assert kid_allergies == ["Peanuts"] and own_allergies == ["Penicillin"]
    assert (await client.get("/me/medications", headers=guest)).json() == []
    assert (await client.get("/me/profile", headers=as_kid)).json()["sex"] == "female"

    # consents and privacy settings are the guardian's, also while the child's profile is active
    assert "core_service" in (await client.get("/consents", headers=as_kid)).json()["active"]
    await client.put("/me/settings", json={"offline_mode": True}, headers=as_kid)
    assert (await client.get("/me/settings", headers=guest)).json()["offline_mode"] is True

    # the family list works from either profile; renaming is allowed
    assert len((await client.get("/family", headers=as_kid)).json()) == 1
    assert (await client.patch(f"/family/{kid}", json={"name": "Asha R"}, headers=guest)).json()["name"] == "Asha R"

    # another account cannot open this profile
    other = {"Authorization": f"Bearer {(await client.post('/auth/guest')).json()['access_token']}"}
    assert (await client.get("/me/allergies", headers={**other, "X-Profile-Id": kid})).status_code == 403
    assert (await client.delete(f"/family/{kid}", headers=other)).status_code == 404

    # removing the profile erases the child's data
    assert (await client.delete(f"/family/{kid}", headers=guest)).status_code == 204
    assert (await client.get("/family", headers=guest)).json() == []
    assert (await client.get("/me/allergies", headers=as_kid)).status_code == 403
    assert [a["substance"] for a in (await client.get("/me/allergies", headers=guest)).json()] == ["Penicillin"]


async def test_account_erasure_includes_family(client, guest):
    kid = (await client.post("/family", json={"name": "Ravi", "relation": "parent"}, headers=guest)).json()["id"]
    await client.post("/me/allergies", json={"substance": "Sulfa"}, headers={**guest, "X-Profile-Id": kid})
    assert (await client.delete("/me", headers=guest)).status_code == 204
    assert (await client.get("/me/allergies", headers=guest)).status_code == 401


async def test_family_requires_core_consent(client):
    tok = (await client.post("/auth/guest")).json()["access_token"]
    r = await client.post("/family", json={"name": "X", "relation": "spouse"}, headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403


async def test_visit_summary(client, guest):
    await client.put("/me/profile", json={"sex": "male", "dob": "1970-01-15", "height_cm": 170}, headers=guest)
    await client.post("/me/vitals", json=[{"type": "weight", "value": 80}, {"type": "bp_sys", "value": 150},
                                          {"type": "bp_dia", "value": 95}], headers=guest)
    await client.post("/me/conditions", json={"display": "Type 2 diabetes"}, headers=guest)
    await client.post("/me/medications", json={"name": "Metformin 500"}, headers=guest)
    s = (await client.get("/me/visit-summary", headers=guest)).json()
    assert s["patient"]["age"] >= 56 and s["patient"]["bmi"] == 27.7
    assert s["conditions"] == ["Type 2 diabetes"] and s["medicines"][0]["name"] == "Metformin 500"
    assert s["bp_readings"][0]["sys"] == 150 and s["bp_readings"][0]["dia"] == 95
    assert s["questions_to_ask"][-1].startswith("When should I come")


def test_questions_to_ask_rules():
    q = questions_to_ask(abnormal=[{"test": "HbA1c", "value": "7.8", "unit": "%", "flag": "H"}],
                         meds=[{}] * 5, checks=[], allergies=[], pregnancy="pregnant")
    assert q[0] == "My HbA1c is high (7.8 %). What does this mean and what should I do?"
    assert any("still needed" in x for x in q) and any("pregnancy" in x for x in q)


async def _raw_triage_rows(uid: str):
    from sqlalchemy import select

    from app.db import SessionLocal, current_account_id, current_user_id
    from app.models import TriageSession
    current_user_id.set(uid)
    current_account_id.set(uid)
    async with SessionLocal() as db:
        return list(await db.scalars(select(TriageSession).where(TriageSession.user_id == uid)))


async def test_symptom_check_state_is_encrypted_at_rest(client, guest):
    import json

    from app.db import SessionLocal
    from app.models import TriageSession, User
    from app.security.crypto import Cipher
    from app.triage.migrate import encrypt_legacy_rows
    from app.triage.store import is_encrypted

    uid = (await client.get("/me/profile", headers=guest)).json()["user_id"]
    # the emergency path stores state without needing the model
    r = await client.post("/triage/sessions", json={"complaint": "crushing chest pain and sweating"}, headers=guest)
    assert r.status_code == 201 and r.json()["red_flag"]["triggered"]
    rows = await _raw_triage_rows(uid)
    assert len(rows) == 1 and is_encrypted(rows[0].state)
    assert "chest" not in json.dumps(rows[0].state)
    assert (await client.get(f"/triage/sessions/{rows[0].id}", headers=guest)).json()["red_flag"]["triggered"]

    # a row saved before encryption existed: still readable, and encrypted once read
    async with SessionLocal() as db:
        legacy = TriageSession(user_id=uid, status="closed", state={"lang": "en", "complaint": "itchy rash on arms",
                                                                    "emergency": {"triggered": True, "kind": "urgent"}})
        db.add(legacy)
        await db.commit()
        lid = legacy.id
    assert (await client.get(f"/triage/sessions/{lid}", headers=guest)).status_code == 200
    assert all(is_encrypted(t.state) for t in await _raw_triage_rows(uid))

    # the one-off migration encrypts any remaining plaintext rows and is idempotent
    async with SessionLocal() as db:
        db.add(TriageSession(user_id=uid, status="closed", state={"lang": "en", "complaint": "knee pain",
                                                                  "emergency": {"triggered": True}}))
        await db.commit()
    done, _, _ = await encrypt_legacy_rows()
    assert done >= 1 and (await encrypt_legacy_rows())[0] == 0
    rows = await _raw_triage_rows(uid)
    assert all(is_encrypted(t.state) for t in rows) and "knee" not in json.dumps([t.state for t in rows])
    async with SessionLocal() as db:
        u = await db.get(User, uid)
    from app.triage.store import load_state
    assert {load_state(t, Cipher(u.dek_wrapped, uid)).get("complaint") for t in rows} >= {"itchy rash on arms", "knee pain"}
