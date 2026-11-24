"""The chat asks questions before naming possibilities: plan → one question at a time → ranked answer."""
import json

from app.triage import chat_flow

PLAN = {"conditions": [{"name": "Viral fever", "prior": 0.45, "specialist": "General Physician"},
                       {"name": "Dengue", "prior": 0.35, "specialist": "General Physician"},
                       {"name": "Typhoid", "prior": 0.2, "specialist": "General Physician"}],
        "findings": [
            {"id": "f1", "question": "Any bleeding from gums or nose?", "red_flag": True, "topic": "general",
             "common_in": ["Dengue"], "rare_in": ["Viral fever", "Typhoid"]},
            {"id": "f2", "question": "Pain behind the eyes?", "red_flag": False, "topic": "general",
             "common_in": ["Dengue"], "rare_in": ["Typhoid"]},
            {"id": "f3", "question": "Stomach pain or constipation?", "red_flag": False, "topic": "general",
             "common_in": ["Typhoid"], "rare_in": ["Viral fever"]},
            {"id": "f4", "question": "Runny nose or sore throat?", "red_flag": False, "topic": "general",
             "common_in": ["Viral fever"], "rare_in": ["Dengue", "Typhoid"]},
            {"id": "f5", "question": "Rash on the body?", "red_flag": False, "topic": "general",
             "common_in": ["Dengue"], "rare_in": ["Viral fever"]}]}


class _Res:
    def __init__(self, data):
        self.text = json.dumps(data)

    def json(self):
        return json.loads(self.text)


def test_read_answer():
    assert chat_flow.read_answer("yes") == "yes" and chat_flow.read_answer("haan") == "yes"
    assert chat_flow.read_answer("no, not at all") == "no" and chat_flow.read_answer("नहीं") == "no"
    assert chat_flow.read_answer("not sure") == "unsure" and chat_flow.read_answer("pata nahi") == "unsure"
    assert chat_flow.read_answer("I also have a terrible headache since this morning and feel dizzy") is None


async def test_question_round_then_ranked(client, guest, monkeypatch):
    from app.db import SessionLocal
    from app.llm import providers
    from tests.conftest import current_user_for

    async def fake_chat(messages, **kw):
        return _Res(PLAN)
    monkeypatch.setattr(providers, "clinical_chat", fake_chat)
    cu = await current_user_for(guest)
    async with SessionLocal() as db:
        step = await chat_flow.start(db, cu, "chat-1", "fever and body ache for 2 days", "adult", 30, "en", "",
                                     allow_cloud=False)
        assert step.kind == "ask"
        assert "bleeding" in step.reply.lower()          # danger sign first
        assert step.structured["clarify"]["options"][0]["value"] == "yes"
    seen = [step.reply]
    for said in ("no", "no", "yes", "no", "both"):
        async with SessionLocal() as db:
            step = await chat_flow.handle_reply(db, cu, "chat-1", said, "", "en")
        if step.kind != "ask":
            break
        seen.append(step.reply)
    assert step.kind == "finish", seen
    assert 2 <= len(step.ranked) <= 3
    assert "RANKING FROM THE SYMPTOM CHECKER" in step.context
    assert step.complaint.startswith("fever")
    assert len(seen) <= chat_flow.CHAT_MAX_QUESTIONS + 1  # + at most the "which way?" choice


async def test_red_flag_yes_finishes_at_once(client, guest, monkeypatch):
    from app.db import SessionLocal
    from app.llm import providers
    from tests.conftest import current_user_for

    async def fake_chat(messages, **kw):
        return _Res(PLAN)
    monkeypatch.setattr(providers, "clinical_chat", fake_chat)
    cu = await current_user_for(guest)
    async with SessionLocal() as db:
        await chat_flow.start(db, cu, "chat-2", "fever", "adult", 30, "en", "", allow_cloud=False)
    async with SessionLocal() as db:
        step = await chat_flow.handle_reply(db, cu, "chat-2", "yes", "", "en")
    assert step.kind == "finish" and step.urgency in ("see_doctor_24h", "emergency")


async def test_new_description_stops_the_questions(client, guest, monkeypatch):
    from app.db import SessionLocal
    from app.llm import providers
    from tests.conftest import current_user_for

    async def fake_chat(messages, **kw):
        return _Res(PLAN)
    monkeypatch.setattr(providers, "clinical_chat", fake_chat)
    cu = await current_user_for(guest)
    async with SessionLocal() as db:
        await chat_flow.start(db, cu, "chat-3", "fever", "adult", 30, "en", "", allow_cloud=False)
    async with SessionLocal() as db:
        step = await chat_flow.handle_reply(db, cu, "chat-3", "actually my child also has loose motions since yesterday night", "", "en")
    assert step.kind == "none"


def test_plan_drops_repeated_questions():
    from app.triage import engine
    llm = {"conditions": [{"name": "Common cold", "prior": 0.5}, {"name": "Influenza", "prior": 0.5}],
           "findings": [{"id": "f1", "question": "क्या सांस लेने में तकलीफ है?", "red_flag": True, "common_in": ["Influenza"]},
                        {"id": "f2", "question": "Any fever?", "common_in": ["Influenza"]},
                        {"id": "f3", "question": "क्या सांस लेने में तकलीफ है ?", "red_flag": True, "common_in": ["Influenza"]},
                        {"id": "f2", "question": "Body ache?", "common_in": ["Influenza"]}]}
    st = engine.init_state(llm, sex=None, age=30)
    assert [f["id"] for f in st["findings"]] == ["f1", "f2"]
