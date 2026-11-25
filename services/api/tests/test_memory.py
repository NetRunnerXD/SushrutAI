"""Health memory: values from speech, episodes that carry context, undo, timeline, patterns."""
from app.memory.extract import quick_facts


def test_quick_facts_reads_values_and_duration():
    f = {x.name: x for x in quick_facts("I have fever 101 since 2 days and my BP is 140/90")}
    assert abs(f["temperature"].value["value"] - 38.3) < 0.1
    assert f["temperature"].value["f"] == 101  # shown back as "101 °F", not 100.9 after the °C round trip
    assert f["duration"].value["amount"] == 2 and f["duration"].value["unit"] == "days"
    assert f["bp systolic"].value["value"] == 140
    hi = {x.name: x for x in quick_facts("मुझे तीन दिन से बुखार है")}
    assert hi["duration"].value["amount"] == 3
    hg = {x.name: x for x in quick_facts("bukhar 102 hai do din se")}
    assert "temperature" in hg and hg["duration"].value["amount"] == 2
    assert quick_facts("what is a normal temperature?") == []


async def test_chat_saves_values_and_links_episode(client, guest, monkeypatch):
    from app.memory import extract

    async def fake_model_facts(text):  # the background model pass, made deterministic
        return [extract.Fact("symptom", "fever", {"present": True, "severity": "moderate"}),
                extract.Fact("symptom", "cough", {"present": False})]
    monkeypatch.setattr(extract, "model_facts", fake_model_facts)
    from app.memory.store import save_facts
    from app.security import auth  # noqa: F401

    # simulate the background pass directly (the chat test client cannot wait for background tasks)
    r = await client.post("/me/memory/note", json={"text": "fever 101 for 2 days"}, headers=guest)
    assert r.status_code == 201
    saved = r.json()
    names = {x["name"] for x in saved}
    assert {"temperature", "duration", "note"} <= names
    tl = (await client.get("/me/memory/timeline", headers=guest)).json()
    assert any(i["type"] == "fact" and i["name"] == "temperature" for i in tl)
    temp_id = next(x["id"] for x in saved if x["name"] == "temperature")
    assert (await client.delete(f"/me/memory/facts/{temp_id}", headers=guest)).status_code == 204
    vitals = (await client.get("/me/vitals", headers=guest)).json()
    assert not any(v.get("type") == "temp" for v in (vitals if isinstance(vitals, list) else vitals.get("items", [])))


async def test_episodes_join_by_symptom_and_reading(client, guest):
    from app.db import SessionLocal
    from app.memory.extract import Fact
    from app.memory.store import memory_block, open_episodes, save_facts
    from app.security.auth import CurrentUser  # noqa: F401
    from tests.conftest import current_user_for

    cu = await current_user_for(guest)
    async with SessionLocal() as db:
        await save_facts(db, cu, [Fact("symptom", "fever", {"present": True}), Fact("symptom", "body ache", {"present": True})])
        await save_facts(db, cu, [Fact("vital", "temperature", {"type": "temp", "value": 38.9, "unit": "°C"})])
        await save_facts(db, cu, [Fact("symptom", "loose motion", {"present": True})])
        eps = await open_episodes(db, cu)
        assert len(eps) == 2  # fever thread + a new stomach thread
        fever = next(e for e in eps if "fever" in e.symptoms)
        block = await memory_block(db, cu, "the temperature is 102 now")
        assert "ONGOING" in block
    async with SessionLocal() as db:
        from sqlalchemy import select
        from app.models import HealthFact
        temp = await db.scalar(select(HealthFact).where(HealthFact.name == "temperature", HealthFact.user_id == cu.id))
        assert temp.episode_id == fever.id  # "temperature" joined the fever thread


async def test_insights_count_recurring_symptoms(client, guest):
    from datetime import datetime, timedelta, timezone
    from app.db import SessionLocal
    from app.memory.extract import Fact
    from app.memory.patterns import insights
    from app.memory.store import save_facts
    from tests.conftest import current_user_for

    cu = await current_user_for(guest)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with SessionLocal() as db:
        for d in (1, 8, 15):
            f = Fact("symptom", "loose motion", {"present": True, "trigger": "eating outside"}, observed_at=now - timedelta(days=d))
            await save_facts(db, cu, [f])
        got = await insights(db, cu)
    rec = next(i for i in got if i["type"] == "recurring")
    assert rec["symptom"] == "loose motion" and rec["times"] == 3 and rec["trigger"] == "eating outside"


async def test_visit_summary_has_story_and_spoken(client, guest):
    await client.post("/me/memory/note", json={"text": "fever 101 for 2 days"}, headers=guest)
    r = (await client.get("/me/visit-summary?days=30", headers=guest)).json()
    assert "episodes" in r and "medicines_tried" in r and "documents" in r
    assert r["spoken"].startswith("Health summary")


async def test_explain_depth_adapts(client, guest):
    from app.db import SessionLocal
    from app.llm.orchestrator import _explain_style, adapt_explain_depth
    from tests.conftest import current_user_for

    cu = await current_user_for(guest)
    async with SessionLocal() as db:
        assert await _explain_style(db, cu) == ""
        await adapt_explain_depth(db, cu, "I don't understand")
        assert "EXPLAIN VERY SIMPLY" in await _explain_style(db, cu)
        await adapt_explain_depth(db, cu, "tell me more")
        await adapt_explain_depth(db, cu, "tell me more")
        assert "more detail" in await _explain_style(db, cu)
        await adapt_explain_depth(db, cu, "what is a normal bp")  # no signal: unchanged
        assert "more detail" in await _explain_style(db, cu)


async def test_feedback_feeds_storylines(client, guest):
    r = await client.post("/me/memory/feedback", json={"kind": "medicine", "name": "Paracetamol", "value": "better",
                                                         "note": "fever down in 2 days"}, headers=guest)
    assert r.status_code == 201
    from app.db import SessionLocal
    from app.medicine.resolve import Resolved
    from app.medicine.storyline import _memory_line
    from tests.conftest import current_user_for

    cu = await current_user_for(guest)
    async with SessionLocal() as db:
        line = await _memory_line(db, cu, Resolved("Dolo 650", "printed", True, [{"generic": "paracetamol"}]))
    assert "it helped" in line and "fever down" in line
    assert (await client.post("/me/memory/feedback", json={"kind": "answer", "name": "fever", "value": "up"}, headers=guest)).status_code == 201


async def test_model_facts_drops_empty_triggers_and_unsaid_anchors(monkeypatch):
    from app.llm import providers
    from app.memory import extract

    class R:
        def json(self):
            return {"symptoms": [{"name": "loose motion", "present": True, "severity": "mild", "trigger": "nothing"},
                                 {"name": "stomach pain", "present": True, "trigger": "after eating outside"}],
                    "context": ["since the trip", "after eating outside"]}

    class O:
        async def chat(self, *a, **k):
            return R()

    monkeypatch.setattr(providers, "ollama", lambda: O())
    got = await extract.model_facts("loose motions since morning and stomach pain after eating outside")
    sx = {f.name: f.value for f in got if f.kind == "symptom"}
    assert "trigger" not in sx["loose motion"] and sx["stomach pain"]["trigger"] == "after eating outside"
    assert [f.name for f in got if f.kind == "context"] == ["after eating outside"]  # no trip was mentioned


async def test_same_fact_twice_is_stored_once(client, guest):
    from sqlalchemy import func, select
    from app.db import SessionLocal
    from app.memory.extract import Fact
    from app.memory.store import save_facts
    from app.models import HealthFact
    from tests.conftest import current_user_for

    cu = await current_user_for(guest)
    async with SessionLocal() as db:
        facts = lambda: [Fact("symptom", "fever", {"present": True, "severity": "mild"}),  # noqa: E731
                         Fact("duration", "duration", {"amount": 3, "unit": "days", "days": 3, "onset": "2026-11-23"})]
        a = await save_facts(db, cu, facts())
        b = await save_facts(db, cu, facts())
        assert [x["id"] for x in a] == [x["id"] for x in b]
        n = await db.scalar(select(func.count()).select_from(HealthFact).where(HealthFact.user_id == cu.id))
        assert n == 2
