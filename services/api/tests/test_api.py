import json

import pytest

from app.llm.providers import Groq, PrivacyViolation
from app.pipelines.drugs import ingest_janaushadhi
from tests.conftest import JA_ROWS


async def test_consent_required_for_chat(client):
    h = {"Authorization": f"Bearer {(await client.post('/auth/guest')).json()['access_token']}"}
    sid = (await client.post("/chat/sessions", json={"lang": "en"}, headers=h)).json()["id"]
    r = await client.post(f"/chat/sessions/{sid}/messages", json={"text": "hello"}, headers=h)
    assert r.status_code == 403


async def test_privacy_notice_has_all_purposes(client):
    r = (await client.get("/privacy/notice", params={"lang": "hi"})).json()
    assert {p["purpose"] for p in r["purposes"]} >= {"core_service", "history_storage", "cloud_speech"}
    assert "108" in r["disclaimer"]


async def test_profile_bmi_vitals_export_erase(client, guest):
    r = await client.put("/me/profile", json={"dob": "1980-05-01", "sex": "male", "height_cm": 170,
                                              "blood_group": "B+", "diet": "veg", "preferred_lang": "hi"}, headers=guest)
    assert r.status_code == 200 and r.json()["preferred_lang"] == "hi"
    assert (await client.post("/me/vitals", json=[{"type": "weight", "value": 70}], headers=guest)).status_code == 201
    bad = await client.post("/me/vitals", json=[{"type": "spo2", "value": 140}], headers=guest)
    assert bad.status_code == 422
    prof = (await client.get("/me/profile", headers=guest)).json()
    assert prof["bmi"]["category"] == "overweight" and prof["dob"] == "1980-05-01"
    await client.post("/me/allergies", json={"substance": "Penicillin", "severity": "severe"}, headers=guest)
    exp = (await client.get("/me/export", headers=guest)).json()
    assert exp["allergies"][0]["substance"] == "Penicillin"
    assert (await client.delete("/me", headers=guest)).status_code == 204
    assert (await client.get("/me/profile", headers=guest)).status_code == 401


async def test_phi_is_encrypted_at_rest(client, guest, tmpdir_path):
    import os
    if os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("checks the SQLite file; on Postgres see test_rls_isolation")
    await client.put("/me/profile", json={"emergency_contact": "Sunita 9876543210"}, headers=guest)
    raw = (tmpdir_path / "test.db").read_bytes()
    assert b"Sunita" not in raw and b"9876543210" not in raw


async def test_email_otp_flow_dev_mode(client):
    r = (await client.post("/auth/otp/request", json={"email": "test@example.org"})).json()
    code = r["dev_code"]
    bad = await client.post("/auth/otp/verify", json={"email": "test@example.org", "code": "000000" if code != "000000" else "111111"})
    assert bad.status_code == 400
    ok = await client.post("/auth/otp/verify", json={"email": "test@example.org", "code": code})
    assert ok.status_code == 200 and ok.json()["is_guest"] is False


async def test_chat_emergency_short_circuits_without_model(client, guest):
    sid = (await client.post("/chat/sessions", json={"lang": "hi"}, headers=guest)).json()["id"]
    r = await client.post(f"/chat/sessions/{sid}/messages", json={"text": "seene me dard aur pasina", "lang": "hi"},
                          headers=guest)
    events = [e for e in r.text.split("\n\n") if e.startswith("event:")]
    names = [e.split("\n")[0][7:] for e in events]
    assert "red_flag" in names and "answer" in names
    answer = json.loads(next(e for e in events if e.startswith("event: answer")).split("data: ", 1)[1])
    assert "108" in answer["reply"]


async def test_janaushadhi_ingest_search_alternatives(client, tmpdir_path):
    p = tmpdir_path / "ja.json"
    p.write_text(json.dumps({"responseBody": {"newProductResponsesList": JA_ROWS}}), encoding="utf-8")
    await ingest_janaushadhi(p)
    from app.medicine.service import INDEX
    INDEX.loaded = False
    res = (await client.get("/medicines/search", params={"q": "etoricoxib 120"})).json()["results"]
    assert res[0]["name"] == "Etoricoxib Tablets IP 120 mg"
    assert res[0]["price"]["per_unit_inr"] == pytest.approx(3.56, abs=0.01)
    by = (await client.post("/medicines/alternatives/by-composition",
                            json={"text": "Paracetamol 325mg + Ibuprofen 400mg tablet"})).json()
    assert by["alternatives"][0]["is_janaushadhi"] and by["alternatives"][0]["price"]["mrp_inr"] == 8.25


async def test_groq_never_receives_images():
    g = Groq()
    g.key = "x"
    with pytest.raises(PrivacyViolation):
        await g.chat("m", [{"role": "user", "content": "x"}], images=["aGVsbG8="])


async def test_rls_isolation_between_users(client):
    """User B must never see user A's rows (API level; on Postgres also enforced by row-level security)."""
    async def new_user():
        h = {"Authorization": f"Bearer {(await client.post('/auth/guest')).json()['access_token']}"}
        await client.post("/consents", json={"purposes": ["core_service"]}, headers=h)
        return h
    a, b = await new_user(), await new_user()
    await client.post("/me/allergies", json={"substance": "Sulfa"}, headers=a)
    assert len((await client.get("/me/allergies", headers=a)).json()) == 1
    assert (await client.get("/me/allergies", headers=b)).json() == []


async def test_postgres_rls_blocks_unscoped_reads():
    import os
    if not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("Postgres only")
    from sqlalchemy import text
    from app.db import SessionLocal, current_user_id
    tok = current_user_id.set(None)
    try:
        async with SessionLocal() as db:
            n = (await db.execute(text("select count(*) from allergies"))).scalar()
        assert n == 0  # without app.user_id no PHI row is visible, even to the table owner (FORCE RLS)
    finally:
        current_user_id.reset(tok)
