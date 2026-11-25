"""Nearest hospitals from OpenStreetMap, the responder summary, and the voice command."""
from app.assistant.actions import parse
from app.routers.emergency import parse_overpass

DATA = {"elements": [
    {"type": "node", "lat": 19.08, "lon": 72.88, "tags": {"amenity": "hospital", "name": "City Hospital", "phone": "+91 22 1234 5678", "emergency": "yes"}},
    {"type": "way", "center": {"lat": 19.10, "lon": 72.90}, "tags": {"amenity": "hospital", "name": "Far Hospital"}},
    {"type": "node", "lat": 19.075, "lon": 72.877, "tags": {"emergency": "ambulance_station", "name": "Ambulance Point"}},
    {"type": "node", "lat": 19.081, "lon": 72.881, "tags": {"amenity": "hospital"}},  # no name: skipped
]}


def test_parse_overpass_sorts_and_reads_phones():
    got = parse_overpass(DATA, 19.076, 72.877)
    names = [p["name"] for p in got]
    assert "Far Hospital" == names[-1] and len(got) == 3
    city = next(p for p in got if p["name"] == "City Hospital")
    assert city["phone"] == "+91 22 1234 5678" and city["emergency_dept"]
    assert next(p for p in got if p["name"] == "Ambulance Point")["kind"] == "ambulance"


def test_call_nearest_voice_command():
    for t in ["call the nearest hospital", "call ambulance near me", "nazdeeki hospital ko call karo"]:
        a = parse(t)
        assert a is not None and a.action == "call_nearest", t
    assert parse("which hospital is nearest?") is None or parse("which hospital is nearest?").action != "call_nearest"


async def test_nearby_respects_offline(client, guest, monkeypatch):
    monkeypatch.setenv("OFFLINE_MODE", "1")
    r = await client.get("/emergency/nearby?lat=19.07&lon=72.87", headers=guest)
    assert r.status_code == 503


async def test_responder_summary(client, guest):
    r = (await client.get("/emergency/summary?symptoms=chest pain&lat=19.07601&lon=72.87765", headers=guest)).json()
    assert r["text"].startswith("This is an emergency call") and "chest pain" in r["text"] and "19.076" in r["text"]


async def test_nearby_tries_the_next_map_server(client, guest, monkeypatch):
    import httpx
    from app.routers import emergency

    tried = []

    async def fake_post(self, url, data=None, **k):
        tried.append(url)
        req = httpx.Request("POST", url)
        if url == emergency.OVERPASS_MIRRORS[0]:
            return httpx.Response(502, request=req)
        return httpx.Response(200, json=DATA, request=req)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    r = (await client.get("/emergency/nearby?lat=19.076&lon=72.877", headers=guest)).json()
    assert tried[:2] == list(emergency.OVERPASS_MIRRORS[:2])
    assert not r["unavailable"] and r["places"][0]["name"] in ("City Hospital", "Ambulance Point")


async def test_nearby_says_when_no_map_server_answers(client, guest, monkeypatch):
    import httpx

    async def down(self, url, data=None, **k):
        return httpx.Response(502, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", down)
    r = (await client.get("/emergency/nearby?lat=19.076&lon=72.877", headers=guest)).json()
    assert r["unavailable"] and r["places"] == [] and r["national"][0]["phone"] == "108"
