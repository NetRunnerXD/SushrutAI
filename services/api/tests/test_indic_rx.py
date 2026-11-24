"""Handwritten prescriptions with Indian-language instructions: normalised before structuring; English unchanged."""
import asyncio

from app.ocr.indic import normalise_line, normalise_transcript, script_of
from app.ocr.transcript import parse_transcript


def test_english_lines_untouched():
    line = "MED: Tab. Dolo 650 mg 1 BD x 5 days after food"
    assert normalise_line(line) == line
    t = "MED: Tab Pan 40 OD\nTESTS: none\nDIAGNOSIS: none"
    assert asyncio.run(normalise_transcript(t)) == t


def test_local_dosing_words():
    assert "BD" in normalise_line("MED: Tab Dolo 650 दिन में दो बार खाने के बाद ५ दिन")
    assert "after food" in normalise_line("MED: Tab Dolo 650 দিনে দুবার খাবার পরে")
    assert "TDS" in normalise_line("MED: Cap Amoxy 500 દિવસમાં ત્રણ વાર")
    assert "5 days" in normalise_line("MED: Tab Dolo ५ दिन")
    assert script_of("দিনে দুবার") == "bn" and script_of("દિવસ") == "gu"


def test_parsed_frequency_from_hindi_instruction():
    t = asyncio.run(normalise_transcript("MED: Tab Dolo 650 दिन में दो बार खाने के बाद ५ दिन\nTESTS: none"))
    meds = parse_transcript(t)["medicines"]
    assert meds and meds[0]["name"].lower().startswith("dolo")
