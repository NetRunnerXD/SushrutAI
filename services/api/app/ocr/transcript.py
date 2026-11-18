"""Two-step prescription reading, step 2: turn a VLM's plain-text transcription into medicine lines.

Small vision models (Qwen3-VL-4B, Qwen2.5-VL-3B, MedGemma-4B) transcribe handwriting far better in plain text
than under a forced JSON schema — with JSON, Qwen3-VL returned empty lists on most handwritten pages; in plain
text it transcribed them in ~2 s. So the model only transcribes, and this module does the structuring.
"""
from __future__ import annotations

import re

TRANSCRIBE_PROMPT = """This is an Indian doctor's prescription (printed and/or handwritten).
Transcribe every MEDICINE line exactly as written — the medicine name, strength, dose, how often and how long —
one medicine per line, starting each line with "MED: ".
Medicine lines usually start with Tab, Cap, Syp, Inj, Susp, Drops, Sachet, Oint or a number.
Then write one line starting "TESTS: " with any tests/investigations written (or "none"),
and one line starting "DIAGNOSIS: " with the diagnosis if written (or "none").
Do not add anything that is not written on the page.{ocr_hint}"""

# Longest alternatives first so "Syrup" is not cut to "up" by "Syr".
_FORMS_ALT = (r"tablets?|tabs?|capsules?|caps?|syrup|syr|syp|injection|inj|suspension|susp|sachet|ointment|oint|"
              r"drops?|gel|cream|lotion|tb|t|c")
_NUMBERING = r"(?:\d{1,2}\s*[.)/]\s*|[-•*·]\s*)?"
_FORM_PREFIX = re.compile(rf"(?i)^\s*{_NUMBERING}(?:(?:{_FORMS_ALT})\b\.?\s*)?")
_MED_START = re.compile(rf"(?i)^\s*{_NUMBERING}(?:{_FORMS_ALT})\b")
_ADVICE = re.compile(rf"(?i)^\s*{_NUMBERING}(adv|advice|rest|diet|review|follow|plenty|avoid|admit|"
                     r"nebuli[sz]e|ice|warm|cbc|tests?|investigations?|diagnosis|dx|c/o|o/e|bp|pulse)\b")

_STOP_WORD = re.compile(r"(?i)^(one|two|three|half|once|twice|thrice|daily|at|before|after|with|for|every|"
                        r"tabs?|caps?|ml|od|bd|tds|qid|hs|sos|stat|x|iv|im|sc|po|if|in|each|per|then|morning|night|"
                        r"weekly|monthly|alternate|days?|weeks?|months?|till|until|apply|local|l/a)$")
_NUM = re.compile(r"^\d+(?:\.\d+)?$")
_NUM_UNIT = re.compile(r"(?i)^\d+(?:\.\d+)?(?:mg|mcg|gm|g|ml|iu|%)$")
_UNIT = re.compile(r"(?i)^(mg|mcg|gm|g|ml|iu|%)$")

# Frequent VLM misreads of handwritten Rx shorthand
_SHORTHAND_FIXES = [
    (re.compile(r"(?<![\w.])(?:505|5o5|s0s|50s|5os)(?![\w.])", re.I), "SOS"),
    (re.compile(r"(?<![\w.])(?:8D|B0)(?![\w.])"), "BD"),
    (re.compile(r"(?<![\w.])0D(?![\w.])", re.I), "OD"),
    (re.compile(r"(?<![\w.])(?:TD5|T0S)(?![\w.])", re.I), "TDS"),
    (re.compile(r"(?<![\w.])H5(?![\w.])"), "HS"),
]


def fix_shorthand(line: str) -> str:
    for pat, rep in _SHORTHAND_FIXES:
        line = pat.sub(rep, line)
    return line


def medicine_name(line: str) -> str:
    """'Tab. Celin 500 mg 1 BD' -> 'Celin 500 mg';  'Tab. Montair FX 1 OD' -> 'Montair FX';
    'Syrup Allegra: Fexofenadine(30 mg)' -> 'Allegra';  'Cap Uprise D3 60K weekly' -> 'Uprise D3 60K'."""
    body = _FORM_PREFIX.sub("", line, count=1).strip()
    body = re.split(r"[:(;,]|\s[-–—]\s|\s[xX]\s*\d", body, maxsplit=1)[0].strip()
    toks = body.split()
    name: list[str] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        if _STOP_WORD.match(t) or _NUM.match(t) or _NUM_UNIT.match(t) or re.match(r"^\d+-\d+", t):
            break
        name.append(t)
        i += 1
    if not name or not re.match(r"[A-Za-z]", name[0]):
        return ""
    strength = ""
    if i < len(toks):
        t = toks[i]
        if _NUM_UNIT.match(t):
            strength = t
        elif _NUM.match(t) and i + 1 < len(toks) and _UNIT.match(toks[i + 1]):
            strength = f"{t} {toks[i + 1]}"
        elif _NUM.match(t) and float(t) >= 10:  # bare "40", "500", "650" — not dose counts like "1" or "2"
            strength = t
    return " ".join([*name, strength]).strip(" .")


def _key(line: str) -> str:
    return re.sub(r"\W", "", re.sub(rf"^\s*{_NUMBERING}", "", line).lower())


def parse_transcript(text: str) -> dict:
    meds, tests, diagnosis, seen = [], [], [], set()
    for raw in (text or "").splitlines():
        line = raw.strip().strip("`").strip()
        if not line:
            continue
        upper = line.upper()
        if upper.startswith("TESTS:"):
            v = line[6:].strip()
            if v and v.lower() not in ("none", "nil", "-"):
                tests += [t.strip() for t in re.split(r"[,;]", v) if t.strip()]
            continue
        if upper.startswith("DIAGNOSIS:"):
            v = line[10:].strip()
            if v and v.lower() not in ("none", "nil", "-"):
                diagnosis.append(v)
            continue
        if upper.startswith("MED:"):
            line = line[4:].strip()
        elif not _MED_START.match(line):
            continue  # unlabelled prose — only accept lines that look like medicine orders
        if not line or _ADVICE.match(line):
            continue
        line = fix_shorthand(line)
        name = medicine_name(line)
        if not name or len(re.sub(r"[^A-Za-z]", "", name)) < 2:
            continue
        k = _key(line)
        if k in seen:  # models sometimes repeat a line verbatim
            continue
        seen.add(k)
        meds.append({"line": line, "name": name})
    return {"medicines": meds[:15], "tests": tests[:12], "diagnosis": diagnosis[:6]}
