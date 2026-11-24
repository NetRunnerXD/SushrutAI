"""Chat modules: follow-ups ('check in with me'), trusted-page lookup, erasure covers follow-ups."""


async def test_follow_ups_roundtrip(client, guest):
    r = await client.post("/me/follow-ups", json={"text": "How is your fever today?", "in_days": 0}, headers=guest)
    assert r.status_code == 201 and r.json()[0]["text"] == "How is your fever today?"
    r = await client.post("/me/follow-ups", json={"text": "how is your fever today?", "in_days": 1}, headers=guest)
    assert len(r.json()) == 1  # duplicates are not added
    await client.post("/me/follow-ups", json={"text": "Any new rash?", "in_days": 2}, headers=guest)
    due = (await client.get("/me/follow-ups?due=true", headers=guest)).json()
    assert [f["text"] for f in due] == ["How is your fever today?"]
    left = (await client.delete(f"/me/follow-ups/{due[0]['id']}", headers=guest)).json()
    assert [f["text"] for f in left] == ["Any new rash?"]


async def test_follow_ups_erased(client, guest):
    await client.post("/me/follow-ups", json={"text": "Check your BP again", "in_days": 1}, headers=guest)
    assert (await client.delete("/me", headers=guest)).status_code == 204


async def test_topic_lookup_shape(client, guest):
    r = await client.get("/search/topic?q=dengue", headers=guest)
    assert r.status_code == 200
    body = r.json()
    assert body is None or {"title", "url", "source", "snippet"} <= body.keys()


async def test_month_calendar(client, guest):
    from datetime import date
    await client.post("/me/follow-ups", json={"text": "How is the cough?", "in_days": 0}, headers=guest)
    r = await client.get("/me/calendar", headers=guest)
    assert r.status_code == 200
    body = r.json()
    today = date.today().isoformat()
    assert body["month"] == today[:7]
    assert any(e["type"] == "follow_up" for e in body["days"].get(today, {}).get("events", []))
    assert (await client.get("/me/calendar?month=2026-13", headers=guest)).status_code == 422


async def test_calendar_my_own_events(client, guest):
    from datetime import date
    today = date.today().isoformat()
    r = await client.post("/me/calendar/events", json={"day": today, "title": "Dr Rao, 4 pm", "kind": "appointment", "time": "16:00"}, headers=guest)
    assert r.status_code == 201
    eid = r.json()["id"]
    await client.post("/me/calendar/events", json={"day": today, "title": "Morning walk", "kind": "reminder", "time": "07:30"}, headers=guest)
    evs = (await client.get("/me/calendar", headers=guest)).json()["days"][today]["events"]
    mine = [e for e in evs if e["type"] == "mine"]
    assert [e["title"] for e in mine] == ["Morning walk", "Dr Rao, 4 pm"]  # timed, in time order
    assert (await client.post("/me/calendar/events", json={"day": today, "title": "x", "time": "25:00"}, headers=guest)).status_code == 422
    assert (await client.delete(f"/me/calendar/events/{eid}", headers=guest)).status_code == 204
    assert (await client.delete(f"/me/calendar/events/{eid}", headers=guest)).status_code == 404
