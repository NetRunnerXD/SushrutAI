"""Handwritten prescriptions with Indian-language writing (Hindi, Bengali, Gujarati, Marathi, Tamil, …).

Applied to the reader's transcript before it is structured (app/ocr/transcript.py), and only to lines that contain
an Indian script — printed English prescriptions pass through unchanged:
1. dosing instructions in the local language become the standard shorthand the parser understands
   ("दिन में दो बार" → "BD", "খাবার পরে" → "after food", "રાત્રે" → "at night");
2. a medicine name written in an Indian script is rewritten in English spelling (IndicTrans2, local) so it can be
   matched to the drug catalogue ("पैरासिटामोल" → "Paracetamol").
"""
from __future__ import annotations

import asyncio
import re

SCRIPTS = [("hi", 0x0900, 0x097F), ("bn", 0x0980, 0x09FF), ("pa", 0x0A00, 0x0A7F), ("gu", 0x0A80, 0x0AFF),
           ("or", 0x0B00, 0x0B7F), ("ta", 0x0B80, 0x0BFF), ("te", 0x0C00, 0x0C7F), ("kn", 0x0C80, 0x0CFF),
           ("ml", 0x0D00, 0x0D7F), ("ur", 0x0600, 0x06FF)]

# Local-language dosing phrases → the English shorthand app/ocr/abbreviations.py reads. Longest first.
PHRASES: list[tuple[str, str]] = [
    # Hindi / Marathi (Devanagari)
    ("दिन में तीन बार", "TDS"), ("दिन में दो बार", "BD"), ("दिन में एक बार", "OD"), ("तीन बार", "TDS"), ("दो बार", "BD"),
    ("एक बार", "OD"), ("खाने के बाद", "after food"), ("खाने से पहले", "before food"), ("खाली पेट", "empty stomach"),
    ("भोजन के बाद", "after food"), ("भोजन से पहले", "before food"), ("जेवणानंतर", "after food"), ("जेवणापूर्वी", "before food"),
    ("सुबह-शाम", "BD"), ("सुबह शाम", "BD"), ("सुबह", "morning"), ("दोपहर", "afternoon"), ("रात को", "at night"),
    ("रात्री", "at night"), ("रात", "at night"), ("सकाळी", "morning"), ("ज़रूरत पड़ने पर", "SOS"), ("जरूरत पर", "SOS"),
    ("दिन", "days"), ("दिवस", "days"), ("हफ्ते", "weeks"), ("सप्ताह", "weeks"), ("महीने", "months"),
    # Bengali / Assamese
    ("দিনে তিনবার", "TDS"), ("দিনে দুবার", "BD"), ("দিনে একবার", "OD"), ("খাবার পরে", "after food"),
    ("খাওয়ার পরে", "after food"), ("খাবার আগে", "before food"), ("খালি পেটে", "empty stomach"), ("সকালে", "morning"),
    ("রাতে", "at night"), ("দিন", "days"),
    # Gujarati
    ("દિવસમાં ત્રણ વાર", "TDS"), ("દિવસમાં બે વાર", "BD"), ("દિવસમાં એક વાર", "OD"), ("જમ્યા પછી", "after food"),
    ("જમ્યા પહેલાં", "before food"), ("સવારે", "morning"), ("રાત્રે", "at night"), ("દિવસ", "days"),
    # Tamil / Telugu / Kannada / Malayalam (common ones)
    ("சாப்பிட்ட பின்", "after food"), ("சாப்பிடும் முன்", "before food"), ("காலை", "morning"), ("இரவு", "at night"),
    ("భోజనం తర్వాత", "after food"), ("ఉదయం", "morning"), ("రాత్రి", "at night"),
    ("ಊಟದ ನಂತರ", "after food"), ("ಬೆಳಿಗ್ಗೆ", "morning"), ("ರಾತ್ರಿ", "at night"),
    ("ഭക്ഷണത്തിന് ശേഷം", "after food"), ("രാവിലെ", "morning"), ("രാത്രി", "at night"),
    # Punjabi / Urdu
    ("ਖਾਣੇ ਤੋਂ ਬਾਅਦ", "after food"), ("ਸਵੇਰੇ", "morning"), ("ਰਾਤ ਨੂੰ", "at night"),
    ("کھانے کے بعد", "after food"), ("صبح", "morning"), ("رات", "at night"),
]
PHRASES.sort(key=lambda p: -len(p[0]))
_INDIC = re.compile(r"[ऀ-ൿ؀-ۿ]")
_DIGITS = str.maketrans("०१२३४५६७८९০১২৩৪৫৬৭৮৯૦૧૨૩૪૫૬૭૮૯", "012345678901234567890123456789")


def script_of(text: str) -> str | None:
    counts: dict[str, int] = {}
    for ch in text:
        cp = ord(ch)
        for code, lo, hi in SCRIPTS:
            if lo <= cp <= hi:
                counts[code] = counts.get(code, 0) + 1
    return max(counts, key=counts.get) if counts else None


def normalise_line(line: str) -> str:
    """Local-language dosing words → shorthand; local digits → 0-9 (only touches Indian-script text)."""
    if not _INDIC.search(line):
        return line
    out = line.translate(_DIGITS)
    for local, en in PHRASES:
        out = out.replace(local, f" {en} ")
    return re.sub(r"\s+", " ", out).strip()


async def normalise_transcript(transcript: str) -> str:
    """The reader's transcript with Indian-language instructions normalised and Indian-script medicine names
    rewritten in English spelling. Unchanged when it has no Indian script."""
    if not _INDIC.search(transcript or ""):
        return transcript
    lines = transcript.split("\n")
    out: list[str] = []
    to_latin: list[tuple[int, str, str]] = []  # (line index, the Indian-script name, its language)
    for i, ln in enumerate(lines):
        n = normalise_line(ln)
        if n.upper().startswith("MED:"):
            body = n[4:].strip()
            m = re.match(r"^((?:tab|cap|syp|syrup|inj|drops?)\.?\s+)?([^\dA-Za-z]+?)(?=\s+\d|\s+[A-Za-z]|$)", body, re.I)
            if m and _INDIC.search(m.group(2) or ""):
                to_latin.append((i, m.group(2).strip(), script_of(m.group(2)) or "hi"))
        out.append(n)
    if to_latin:
        try:
            from app.speech import models as speech

            by_lang: dict[str, list[tuple[int, str]]] = {}
            for i, name, lang in to_latin:
                by_lang.setdefault(lang, []).append((i, name))
            for lang, items in by_lang.items():
                if lang not in speech.LANGS:
                    continue
                latin = await asyncio.to_thread(speech.translate_many, [n for _, n in items], lang, "en")
                for (i, name), lat in zip(items, latin):
                    lat = re.sub(r"[^A-Za-z0-9 \-]", "", lat).strip()
                    if lat:
                        out[i] = out[i].replace(name, lat.title(), 1)
        except Exception:  # noqa: BLE001 — names stay as written; the person corrects them in the review step
            pass
    return "\n".join(out)
