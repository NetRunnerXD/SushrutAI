"""Chat history management, the streamed draft reply, and warning signs split across messages."""
import json

from app.llm.orchestrator import partial_reply


def _events(body: str) -> list[tuple[str, dict]]:
    out = []
    for block in body.split("\n\n"):
        if block.startswith("event:"):
            name, data = block.split("\n", 1)
            out.append((name[7:], json.loads(data.split("data: ", 1)[1])))
    return out


async def _chat_with_one_question(client, guest, text="seene me dard aur pasina") -> str:
    sid = (await client.post("/chat/sessions", json={"lang": "hi"}, headers=guest)).json()["id"]
    await client.post(f"/chat/sessions/{sid}/messages", json={"text": text, "lang": "hi"}, headers=guest)
    return sid


async def test_rename_pin_and_delete_session(client, guest):
    sid = await _chat_with_one_question(client, guest)
    listed = (await client.get("/chat/sessions", headers=guest)).json()
    row = next(s for s in listed if s["id"] == sid)
    assert row["title"].startswith("seene") and row["pinned"] is False and row["count"] == 2

    assert (await client.patch(f"/chat/sessions/{sid}", json={"title": "Chest pain night", "pinned": True},
                               headers=guest)).status_code == 200
    row = (await client.get("/chat/sessions", headers=guest)).json()[0]  # pinned chats come first
    assert row["id"] == sid and row["title"] == "Chest pain night" and row["pinned"] is True

    await client.patch(f"/chat/sessions/{sid}", json={"title": ""}, headers=guest)  # empty = first question again
    row = next(s for s in (await client.get("/chat/sessions", headers=guest)).json() if s["id"] == sid)
    assert row["title"].startswith("seene")

    assert (await client.delete(f"/chat/sessions/{sid}", headers=guest)).status_code == 204
    assert (await client.get(f"/chat/sessions/{sid}", headers=guest)).status_code == 404
    assert all(s["id"] != sid for s in (await client.get("/chat/sessions", headers=guest)).json())


async def test_cannot_touch_someone_elses_chat(client, guest):
    sid = await _chat_with_one_question(client, guest)
    tok = (await client.post("/auth/guest")).json()["access_token"]
    other = {"Authorization": f"Bearer {tok}"}
    assert (await client.patch(f"/chat/sessions/{sid}", json={"pinned": True}, headers=other)).status_code == 404
    assert (await client.delete(f"/chat/sessions/{sid}", headers=other)).status_code == 404


async def test_warning_signs_split_across_messages_are_caught(client, guest):
    await client.delete("/consents/history_storage", headers=guest)  # history then comes from the client
    sid = (await client.post("/chat/sessions", json={"lang": "en"}, headers=guest)).json()["id"]
    history = [{"role": "user", "content": "I suddenly feel weakness"},
               {"role": "assistant", "content": "Can you tell me more?"}]
    r = await client.post(f"/chat/sessions/{sid}/messages", headers=guest,
                          json={"text": "in my right arm and my face", "lang": "en", "history": history})
    names = [n for n, _ in _events(r.text)]
    assert "red_flag" in names  # stroke signs, although neither message alone matches


def test_edit_history_is_a_prefix_of_the_saved_thread():
    from app.routers.chat import _history_prefix
    stored = [{"role": "user", "content": "fever"}, {"role": "assistant", "content": "rest"}]
    assert _history_prefix([{"role": "user", "content": "fever"}], stored)
    assert _history_prefix([], stored)  # edited back to the first question
    assert _history_prefix(stored, stored)
    assert not _history_prefix([{"role": "user", "content": "cough"}], stored)
    assert not _history_prefix(stored + [{"role": "user", "content": "more"}], stored)


def test_partial_reply_decodes_a_reply_still_being_written():
    assert partial_reply('{"reply": "Hello\\nwor') == "Hello\nwor"
    assert partial_reply('{"reply": "a \\u0939') == "a ह"
    assert partial_reply('{"reply": "a \\u09') == "a "  # escape cut in half is dropped, not shown raw
    assert partial_reply('{"reply": "say \\"x\\') == 'say "x'
    assert partial_reply('{"tips": []}') == ""



async def test_delete_all_chats(client, guest):
    a = await _chat_with_one_question(client, guest)
    b = await _chat_with_one_question(client, guest, "mujhe sar dard hai")
    assert (await client.get("/chat/sessions", headers=guest)).json()
    assert (await client.delete("/chat/sessions", headers=guest)).status_code == 204
    assert (await client.get("/chat/sessions", headers=guest)).json() == []
    assert (await client.get(f"/chat/sessions/{a}", headers=guest)).status_code == 404
    assert (await client.delete("/chat/sessions", headers=guest)).status_code == 204  # nothing left: still fine
