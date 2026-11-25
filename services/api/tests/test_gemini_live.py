"""Gemini Live setup, transcript events, and the emergency gate. No network."""
import json

from app.speech.gemini_live import (
    emergency_for, emergency_turn, parse_server_message, resolve_model, setup_message, tool_response,
)


def test_each_live_mode_picks_its_model(monkeypatch):
    from app.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "gemini_transcribe_live_model", "gemini-3.5-transcribe-live")
    monkeypatch.setattr(s, "gemini_translate_live_model", "gemini-3.5-live-translate-preview")
    monkeypatch.setattr(s, "gemini_live_model", "gemini-3.8-live")
    monkeypatch.setattr(s, "gemini_flash_live_model", "gemini-3.1-flash-live-preview")
    assert resolve_model("transcribe") == "gemini-3.5-transcribe-live"
    assert resolve_model("translate") == "gemini-3.5-live-translate-preview"
    assert resolve_model("talk") == "gemini-3.8-live"
    assert resolve_model("talk", "flash") == "gemini-3.1-flash-live-preview"


def test_setup_frames_match_the_live_api_shape():
    tr = setup_message("transcribe", "gemini-3.5-transcribe-live", lang="hi")
    assert tr["setup"]["model"] == "models/gemini-3.5-transcribe-live"
    assert tr["setup"]["generationConfig"]["responseModalities"] == ["TEXT"]
    assert tr["setup"]["inputAudioTranscription"]["languageCodes"] == ["hi-IN"]

    tl = setup_message("translate", "gemini-3.5-live-translate-preview", target_lang="hi")
    assert tl["setup"]["generationConfig"]["translationConfig"]["targetLanguageCode"] == "hi"
    assert "outputAudioTranscription" in tl["setup"]

    talk = setup_message("talk", "gemini-3.8-live")
    names = talk["setup"]["tools"][0]["functionDeclarations"][0]["name"]
    assert names == "answer_health_question"
    assert talk["setup"]["generationConfig"]["responseModalities"] == ["AUDIO"]

    flash = setup_message("talk", "gemini-3.1-flash-live-preview")
    assert flash["setup"]["model"].endswith("gemini-3.1-flash-live-preview")


def test_parse_interim_final_audio_and_tool_call():
    events = parse_server_message({"setupComplete": {}})
    assert events[0].json == {"type": "ready"}
    events = parse_server_message({"serverContent": {
        "interimInputTranscription": {"text": "chest"},
        "inputTranscription": {"text": "chest pain"},
        "outputTranscription": {"text": "सीने में दर्द"},
        "modelTurn": {"parts": [{"inlineData": {"data": "YWI="}}]},
    }})
    kinds = [e.json["type"] for e in events if e.json]
    assert kinds == ["interim", "final", "input_transcript", "output_transcript"]
    assert events[-1].audio == b"ab"
    tool = parse_server_message({"toolCall": {"functionCalls": [
        {"id": "c1", "name": "answer_health_question", "args": {"question": "what is fever"}}]}})
    assert tool[0].json["question"] == "what is fever"
    body = json.loads(tool_response("c1", "answer_health_question", "Rest and fluids."))
    assert body["toolResponse"]["functionResponses"][0]["response"]["result"] == "Rest and fluids."


def test_spoken_question_is_answered_in_english_then_translated_back():
    from app.llm.orchestrator import english_pivot
    assert english_pivot(True, "gemini_fast", "bn", "I have a fever", "আমার জ্বর")
    assert english_pivot(True, "balanced", "hi", "fever since morning", "सुबह से बुखार")
    assert not english_pivot(True, "gemini_fast", "en", "I have a fever", "I have a fever")
    assert not english_pivot(False, "gemini_fast", "bn", "I have a fever", "আমার জ্বর")
    assert english_pivot(False, "balanced", "bn", "I have a fever", "আমার জ্বর")


def test_transcript_keeps_the_detected_language():
    events = parse_server_message({"serverContent": {"inputTranscription": {
        "text": "আমার জ্বর", "languageCode": "bn-IN"}}})
    final = next(e.json for e in events if e.json and e.json["type"] == "final")
    assert final["lang"] == "bn" and final["text"] == "আমার জ্বর"


def test_emergency_transcript_blocks_the_answer_tool():
    flag = emergency_for("I have chest pain and sweating", "en")
    assert flag and flag["skip_tool"] and "108" in flag["text"]
    assert emergency_for("what is a balanced diet", "en") is None
    spoken = json.loads(emergency_turn(flag["text"]))
    assert "108" in spoken["clientContent"]["turns"][0]["parts"][0]["text"]
