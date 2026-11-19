"""Phase 5: warnings when adding a medicine, visit questions, refills, household today, chat search."""
from datetime import timedelta

from sqlalchemy import select

from app.models import Interaction
from app.models import _now as utcnow


async def test_allergy_warning_by_drug_class(client, guest):
    await client.post("/me/allergies", json={"substance": "Penicillin"}, headers=guest)
    r = (await client.post("/me/medications", json={"name": "Augmentin 625"}, headers=guest)).json()
    w = [x for x in r["warnings"] if x["kind"] == "allergy"]
    assert w and w[0]["allergy"] == "Penicillin" and "amoxicillin" in w[0]["generics"] and w[0]["level"] == "high"
    ok = (await client.post("/me/medications", json={"name": "Pan 40"}, headers=guest)).json()
    assert ok["warnings"] == []  # pantoprazole: no allergy or interaction


async def test_interaction_warning_with_current_medicine(client, guest):
    from app.db import SessionLocal
    async with SessionLocal() as db:  # a known DDInter pair (public reference data, not per-user)
        exists = (await db.scalars(select(Interaction).where(Interaction.generic_a == "aspirin",
                                                              Interaction.generic_b == "warfarin"))).first()
        if not exists:
            db.add(Interaction(generic_a="aspirin", generic_b="warfarin", severity="Major", source="DDInter"))
            await db.commit()
    await client.post("/me/medications", json={"name": "Warf 5"}, headers=guest)
    r = (await client.post("/me/medications", json={"name": "Ecosprin 75"}, headers=guest)).json()
    w = [x for x in r["warnings"] if x["kind"] == "interaction"]
    assert w and w[0]["with"] == "Warf 5" and w[0]["severity"] == "Major" and w[0]["level"] == "high"


async def test_visit_questions_and_summary(client, guest):
    r = (await client.post("/me/visit-questions", json={"text": "Is my sugar control good enough?", "source": "chat"},
                           headers=guest)).json()
    assert [q["text"] for q in r] == ["Is my sugar control good enough?"]
    r = (await client.post("/me/visit-questions", json={"text": "is my sugar control good enough?"}, headers=guest)).json()
    assert len(r) == 1  # duplicates are not saved twice
    s = (await client.get("/me/visit-summary", headers=guest)).json()
    assert s["my_questions"] == ["Is my sugar control good enough?"]
    r = (await client.delete(f"/me/visit-questions/{r[0]['id']}", headers=guest)).json()
    assert r == []


async def test_refills(client, guest):
    end = (utcnow().date() + timedelta(days=2)).isoformat()
    await client.post("/me/medications", json={"name": "Paracetamol 650 mg tablet", "end_date": end}, headers=guest)
    await client.post("/me/medications", json={"name": "Long course", "end_date": (utcnow().date() + timedelta(days=30)).isoformat()},
                      headers=guest)
    r = (await client.get("/me/refills", headers=guest)).json()
    assert [x["name"] for x in r] == ["Paracetamol 650 mg tablet"] and r[0]["days_left"] in (1, 2, 3)


async def test_household_today(client, guest):
    await client.post("/me/medications", json={"name": "Telma 40", "frequency": {"morning": 1, "per_day": 1}}, headers=guest)
    dob = (utcnow().date() - timedelta(days=44)).isoformat()
    kid = (await client.post("/family", json={"name": "Asha", "relation": "child", "dob": dob}, headers=guest)).json()["id"]
    r = (await client.get("/me/household-today", headers=guest)).json()["people"]
    me, asha = r[0], r[1]
    assert me["relation"] == "self" and me["doses_total"] == 1
    assert asha["name"] == "Asha" and any("Pentavalent" in c["title"] for c in asha["care_due"])
    # works the same while the child's profile is open
    r2 = (await client.get("/me/household-today", headers={**guest, "X-Profile-Id": kid})).json()["people"]
    assert [p["name"] for p in r2] == [None, "Asha"]


async def test_search_finds_text_inside_chat_answers(client, guest):
    sid = (await client.post("/chat/sessions", json={"lang": "en"}, headers=guest)).json()["id"]
    await client.post(f"/chat/sessions/{sid}/messages", json={"text": "Hi"}, headers=guest)  # fixed greeting, no model
    r = (await client.get("/search", params={"q": "what to eat"}, headers=guest)).json()
    hit = [c for c in r["chats"] if c["id"] == sid]
    assert hit and hit[0]["in"] == "assistant" and "what to eat" in hit[0]["snippet"].lower()


async def test_unknown_severity_pairs_are_not_shown(client, guest):
    from app.db import SessionLocal
    async with SessionLocal() as db:
        db.add(Interaction(generic_a="paracetamol", generic_b="pantoprazole", severity="Unknown", source="DDInter"))
        await db.commit()
    await client.post("/me/medications", json={"name": "Dolo 650"}, headers=guest)
    r = (await client.post("/me/medications", json={"name": "Pan 40"}, headers=guest)).json()
    assert r["warnings"] == []
