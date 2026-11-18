"""Vaccines & check-ups schedules, daily check-ins, seasonal tip, and erasure of the new tables."""
from datetime import date, timedelta

from app.care import schedules as S
from app.models import _now as utcnow


def _by_code(items):
    return {i["code"]: i for i in items}


def test_child_schedule_matches_the_national_schedule():
    dob = date(2026, 1, 1)
    items = _by_code(S.child_schedule(dob, date(2026, 2, 13), {}))  # 6 weeks + 1 day old
    assert {c for c, i in items.items() if i["group"] == "6 weeks"} == {"opv1", "penta1", "rvv1", "fipv1", "pcv1"}
    assert {c for c, i in items.items() if i["group"] == "14 weeks"} == {"opv3", "penta3", "fipv2", "rvv3", "pcv2"}
    assert items["penta1"]["status"] == "due" and items["penta2"]["status"] == "soon"  # 27 days away
    assert items["mr1"]["status"] == "upcoming"
    assert items["hepb0"]["status"] == "overdue"  # birth dose is within 24 hours
    assert items["bcg"]["status"] == "due"         # BCG can still be given up to 1 year
    assert "fipv3" not in items and not any("vitamin" in c for c in items)  # not in the source document
    assert items["mr1"]["due_from"] == dob + timedelta(days=round(9 * S.M))


def test_td2_waits_four_weeks_after_the_actual_td1():
    lmp = date(2026, 6, 1)
    done = {"td1": date(2026, 8, 20)}
    td2 = _by_code(S.pregnancy_schedule(lmp, date(2026, 9, 1), done))["td2"]
    assert td2["due_from"] == date(2026, 9, 17) and td2["status"] == "soon"


async def test_care_schedule_api_for_a_child_profile(client, guest):
    dob = utcnow().date() - timedelta(days=7 * 6 + 2)
    kid = (await client.post("/family", json={"name": "Asha", "relation": "child", "dob": dob.isoformat()},
                             headers=guest)).json()["id"]
    h = {**guest, "X-Profile-Id": kid}
    s = (await client.get("/me/care-schedule", headers=h)).json()
    due = {i["code"] for i in s["items"] if i["status"] == "due"}
    assert {"penta1", "opv1", "bcg"} <= due and s["pregnancy"] is None
    s = (await client.post("/me/care-schedule/penta1", json={}, headers=h)).json()
    assert "penta1" not in {i["code"] for i in s["items"]}
    assert _by_code(s["child"]["items"])["penta1"]["status"] == "done"
    s = (await client.delete("/me/care-schedule/penta1", headers=h)).json()
    assert _by_code(s["child"]["items"])["penta1"]["status"] == "due"
    assert (await client.post("/me/care-schedule/nonsense", json={}, headers=h)).status_code == 404
    # the account holder (an adult with no date of birth) has no child schedule
    assert (await client.get("/me/care-schedule", headers=guest)).json()["child"] is None
    ics = (await client.get("/me/reminders.ics", headers=h)).text
    assert "Pentavalent-1 due" in ics and ics.strip().endswith("END:VCALENDAR")


async def test_pregnancy_schedule(client, guest):
    lmp = utcnow().date() - timedelta(weeks=10)
    s = (await client.put("/me/pregnancy", json={"lmp": lmp.isoformat()}, headers=guest)).json()
    assert s["pregnancy"]["weeks"] == 10 and s["pregnancy"]["due_date"] == (lmp + timedelta(days=280)).isoformat()
    due = {i["code"] for i in s["items"] if i["status"] == "due"}
    assert {"anc1", "td1"} <= due
    assert (await client.put("/me/pregnancy", json={"lmp": "2020-01-01"}, headers=guest)).status_code == 422
    s = (await client.put("/me/pregnancy", json={"lmp": None}, headers=guest)).json()
    assert s["pregnancy"] is None


async def test_checkins_tip_and_erasure(client, guest):
    assert (await client.get("/me/checkins", headers=guest)).json()["today"] is None
    await client.post("/me/checkins", json={"mood": "okay"}, headers=guest)
    await client.post("/me/checkins", json={"mood": "unwell", "note": "headache"}, headers=guest)  # updates today
    c = (await client.get("/me/checkins", headers=guest)).json()
    assert c["today"] == "unwell" and len(c["items"]) == 1 and c["items"][0]["note"] == "headache"
    assert (await client.get("/me/visit-summary", headers=guest)).json()["checkins"]["unwell"] == 1
    tip = (await client.get("/me/tip", params={"lang": "hi"}, headers=guest)).json()
    assert tip["season"] in ("monsoon", "summer", "winter") and tip["url"].startswith("https://")
    await client.put("/me/pregnancy", json={"lmp": (utcnow().date() - timedelta(weeks=8)).isoformat()}, headers=guest)
    await client.post("/me/care-schedule/anc1", json={}, headers=guest)
    assert (await client.delete("/me", headers=guest)).status_code == 204
