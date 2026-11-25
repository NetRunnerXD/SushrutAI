"""Conversation controls and the doctor-summary command (voice-operable app)."""
from app.assistant.actions import control_of, parse


def test_controls_match_whole_short_utterances():
    cases = {"stop": "stop", "please stop talking": "stop", "Pause": "pause", "wait": "pause", "go on": "resume",
             "say that again": "repeat", "explain simply": "simpler", "I don't understand": "simpler",
             "tell me more": "more", "go ahead": "yes", "yes please": "yes", "cancel": "no", "never mind": "no",
             "help": "emergency", "call an ambulance": "emergency", "read this page": "read"}
    for text, want in cases.items():
        assert control_of(text) == want, text


def test_controls_never_fire_inside_questions():
    for text in ["stop taking my tablets?", "should I stop my BP medicine", "help me read my report",
                 "is it ok to walk", "what does more than 140 mean", "my right knee hurts", "no appetite for 3 days"]:
        assert control_of(text) is None, text


def test_prepare_summary_command():
    for text in ["prepare summary for doctor", "make a report for the doctor", "doctor summary", "give me my visit summary"]:
        a = parse(text)
        assert a is not None and a.action == "prepare_summary", text
    assert parse("what is a doctor summary?") is None or parse("what is a doctor summary?").action != "prepare_summary"


async def test_parse_endpoint_controls(client, guest):
    r = (await client.post("/assistant/parse", json={"text": "pause", "lang": "en"}, headers=guest)).json()
    assert r["action"] == "control" and r["params"]["intent"] == "pause"
    r = (await client.post("/assistant/parse", json={"text": "prepare summary for doctor", "lang": "hi"}, headers=guest)).json()
    assert r["action"] == "prepare_summary" and r["href"].startswith("/me/visit")
