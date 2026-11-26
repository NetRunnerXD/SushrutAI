"""Values pulled out of what people say (app/memory/extract.quick_facts): temperature, BP, sugar, pulse, weight,
how long it has been going on. Precision / recall / F1 over hand-labelled sentences (English, Hindi, Hinglish).
Gate: F1 >= 0.9 and no value on a sentence that has none.
Run: services/api/.venv/Scripts/python -I -X utf8 eval/extract_eval.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

from app.memory.extract import quick_facts  # noqa: E402

# (sentence, expected {name: value}); temperature in °C (said in °F is converted), duration in days
CASES: list[tuple[str, dict]] = [
    ("I have fever 101 since 2 days", {"temperature": 38.3, "duration": 2}),
    ("fever of 102 F for three days with body ache", {"temperature": 38.9, "duration": 3}),
    ("temperature 99.5 this morning", {"temperature": 37.5}),
    ("my BP is 150/95 today", {"bp systolic": 150, "bp diastolic": 95}),
    ("BP 130 over 85", {"bp systolic": 130, "bp diastolic": 85}),
    ("fasting sugar 126", {"fasting sugar": 126}),
    ("sugar after food was 210", {"after-meal sugar": 210}),
    ("pulse 96 and oxygen 94", {"pulse": 96, "oxygen": 94}),
    ("weight 72 kg", {"weight": 72}),
    ("cough for 2 weeks", {"duration": 14}),
    ("headache since 5 hours", {"duration": 0.21}),
    ("loose motions for 1 day", {"duration": 1}),
    ("मुझे तीन दिन से बुखार है", {"duration": 3}),
    ("बुखार 102 है दो दिन से", {"temperature": 38.9, "duration": 2}),
    ("bukhar 101 hai teen din se", {"temperature": 38.3, "duration": 3}),
    ("sar dard do din se", {"duration": 2}),
    ("बीपी 140/90", {"bp systolic": 140, "bp diastolic": 90}),
    ("शुगर 180 खाने के बाद", {"after-meal sugar": 180}),
    ("vajan 65 kilo", {"weight": 65}),
    ("the fever has been 100 to 101 since yesterday", {"temperature": 37.8}),
    ("my mother has BP 160/100 and headache for 4 days", {"bp systolic": 160, "bp diastolic": 100, "duration": 4}),
    ("temperature was 38.5 in the evening", {"temperature": 38.5}),
    ("knee pain for a month", {"duration": 30}),
    ("rash since 1 week", {"duration": 7}),
    # nothing to extract
    ("what is a normal temperature?", {}),
    ("is 120/80 normal for my age?", {"bp systolic": 120, "bp diastolic": 80}),  # a value, though a question
    ("I feel tired and weak", {}),
    ("can I eat rice in fever", {}),
    ("my son is 5 years old", {}),
    ("take paracetamol 650", {}),
    ("call 108", {}),
]


def close(a: float, b: float) -> bool:
    return abs(a - b) <= max(0.25, 0.02 * abs(b))


def main() -> int:
    tp = fp = fn = 0
    rows = []
    for text, want in CASES:
        got: dict[str, float] = {}
        for f in quick_facts(text):
            v = f.value
            got[f.name] = v.get("days") if f.kind == "duration" else v.get("value")
        hit = {k for k in want if k in got and close(float(got[k]), float(want[k]))}
        tp += len(hit)
        fn += len(want) - len(hit)
        extra = {k for k in got if k not in want or k not in hit}
        fp += len(extra)
        ok = not (set(want) - hit) and not extra
        rows.append({"text": text, "want": want, "got": got, "ok": ok})
        if not ok:
            print("MISS", text, "want", want, "got", got, flush=True)
    p = tp / max(1, tp + fp)
    r = tp / max(1, tp + fn)
    f1 = 2 * p * r / max(1e-9, p + r)
    res = {"precision": round(p, 3), "recall": round(r, 3), "f1": round(f1, 3), "sentences": len(CASES),
           "pass": f1 >= 0.9}
    print(json.dumps(res, indent=2))
    (ROOT / "eval" / "reports" / "extract.json").write_text(json.dumps({**res, "rows": rows}, ensure_ascii=False, indent=1), "utf-8")
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
