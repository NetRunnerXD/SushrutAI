"""'Ask about this' record context, medicine links and universal search."""
import json

from app.llm import orchestrator
from app.llm.providers import LlmResult
from app.safety.guard import GuardResult


def _answer(body: str) -> dict:
    for block in body.split("\n\n"):
        if block.startswith("event: answer"):
            return json.loads(block.split("data: ", 1)[1])
    raise AssertionError(body[:300])


async def test_ask_about_a_record_puts_it_in_the_prompt(client, guest, monkeypatch):
    seen = {}

    async def fake_chat(messages, schema=None, **kw):
        seen.setdefault("prompt", messages[-1]["content"])  # first call (a refusal retry may follow)
        seen["schema"] = schema
        return LlmResult(text=json.dumps({"reply": "Your readings are coming down.", "tips": [], "follow_up_questions": []}),
                         provider="test", model="test", tier="L", latency_ms=1)

    async def safe(text, lang="en"):
        return GuardResult(unsafe=False, categories=[])

    monkeypatch.setattr(orchestrator, "clinical_chat", fake_chat)
    monkeypatch.setattr(orchestrator.guard, "check_user_message", safe)
    await client.post("/me/vitals", json=[{"type": "bp_sys", "value": 150}, {"type": "bp_sys", "value": 138}], headers=guest)
    med = (await client.post("/me/medications", json={"name": "Telma 40", "timing": "after_food"}, headers=guest)).json()
    sid = (await client.post("/chat/sessions", json={"lang": "en"}, headers=guest)).json()["id"]

    r = await client.post(f"/chat/sessions/{sid}/messages", headers=guest,
                          json={"text": "explain this", "context_refs": ["vital:bp_sys", f"med:{med['id']}"]})
    a = _answer(r.text)
    assert "RECORD THE USER IS ASKING ABOUT" in seen["prompt"]
    assert "138" in seen["prompt"] and "150" in seen["prompt"] and "Telma 40" in seen["prompt"]
    assert a["structured"]["kind"] == "report_question"  # a vague "explain this" takes the record's kind
    assert "possible_conditions" not in json.dumps(seen["schema"])

    # someone else's record id is ignored, never resolved
    other = {"Authorization": f"Bearer {(await client.post('/auth/guest')).json()['access_token']}"}
    await client.post("/consents", json={"purposes": ["core_service"]}, headers=other)
    sid2 = (await client.post("/chat/sessions", json={"lang": "en"}, headers=other)).json()["id"]
    seen.clear()
    await client.post(f"/chat/sessions/{sid2}/messages", headers=other,
                      json={"text": "what is this medicine for", "context_refs": [f"med:{med['id']}"]})
    assert "Telma" not in seen.get("prompt", "")


async def test_search_is_scoped_to_the_active_profile(client, guest):
    await client.post("/me/medications", json={"name": "Metformin 500"}, headers=guest)
    await client.post("/me/allergies", json={"substance": "Penicillin"}, headers=guest)
    kid = (await client.post("/family", json={"name": "Asha", "relation": "child"}, headers=guest)).json()["id"]
    await client.post("/me/medications", json={"name": "Calpol 250 syrup"}, headers={**guest, "X-Profile-Id": kid})

    mine = (await client.get("/search", params={"q": "met"}, headers=guest)).json()
    assert [m["name"] for m in mine["my_medicines"]] == ["Metformin 500"]
    assert (await client.get("/search", params={"q": "penic"}, headers=guest)).json()["allergies"] == ["Penicillin"]
    assert (await client.get("/search", params={"q": "calpol"}, headers=guest)).json()["my_medicines"] == []
    kids = (await client.get("/search", params={"q": "calpol"}, headers={**guest, "X-Profile-Id": kid})).json()
    assert [m["name"] for m in kids["my_medicines"]] == ["Calpol 250 syrup"]
    assert (await client.get("/search", params={"q": "x"}, headers=guest)).status_code == 422  # too short
