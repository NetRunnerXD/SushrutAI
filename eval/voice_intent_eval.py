"""Voice control: are spoken commands understood, and are questions never mistaken for commands?

Server path (/assistant/parse → app/assistant/actions.parse) on English / Hinglish utterances with filler words and
misheard variants, questions that contain command words, and other Indian languages through the English pivot.
The instant on-device matcher (apps/web/lib/voiceIntents.ts) is checked too, through node.
Gate: command accuracy >= 95 %, questions taken as commands <= 1 %.
Run: services/api/.venv/Scripts/python -I -X utf8 eval/voice_intent_eval.py
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

from app.assistant.actions import parse  # noqa: E402

CONTROLS = {
    "stop": ["stop", "stop it", "please stop", "stop talking", "be quiet", "enough", "that's enough"],
    "pause": ["pause", "wait", "hold on", "one minute", "just a moment", "pause please"],
    "resume": ["continue", "go on", "resume", "carry on", "start again", "I'm back"],
    "repeat": ["repeat", "repeat that", "say that again", "again", "once more", "what did you say"],
    "simpler": ["explain simply", "explain it more simply", "I don't understand", "in simple words", "make it simple"],
    "more": ["tell me more", "more details", "explain more", "in detail", "read the full answer"],
    "yes": ["yes", "yeah", "okay", "go ahead", "do it", "confirm", "that's right", "yes please"],
    "no": ["no", "cancel", "never mind", "not now", "leave it", "don't"],
    "emergency": ["help", "help me", "emergency", "call an ambulance", "call 108", "sos"],
}
ACTIONS = {
    "prepare_summary": ["prepare summary for doctor", "make a report for the doctor", "doctor summary",
                        "give me my visit summary", "prepare a summary for my doctor please"],
    "call_nearest": ["call the nearest hospital", "call ambulance near me", "phone the closest hospital",
                     "nazdeeki hospital ko call karo"],
    "memory_summary": ["how was my week", "how was my month", "weekly health summary", "monthly summary"],
    "navigate": ["open my medicines", "show my reports", "go to settings", "open my calendar", "take me to family"],
}
QUESTIONS = [
    "should I stop my BP tablets", "can I stop taking paracetamol now", "help me read my report", "is it okay to wait",
    "what does more than 140 mean", "my right knee hurts", "no appetite for 3 days", "tell me more about dengue symptoms?",
    "why do I feel tired again and again", "what is a normal pulse", "how long should I continue the antibiotic",
    "is 130 over 85 normal?", "I have pain behind my eyes and fever", "can I eat rice in fever", "what should I do",
    "should I go to the hospital", "my child has loose motions", "repeat dose after vomiting?", "is it safe to take two",
    "what happens if I miss a dose", "how much water should I drink", "is my sugar report okay",
    "can you explain my blood test", "what are the side effects", "does this medicine cause sleepiness",
    "i feel dizzy when I stand up", "stomach pain after food", "what if I take it with milk",
    "how many days will the fever last", "is cough a sign of TB",
]
PIVOT = [  # other languages: understood through English (IndicTrans2)
    ("ta", "மருத்துவருக்கான சுருக்கத்தை தயார் செய்யுங்கள்", "prepare_summary"),
    ("bn", "ডাক্তারের জন্য সারাংশ তৈরি করুন", "prepare_summary"),
    ("pa", "ਡਾਕਟਰ ਲਈ ਸੰਖੇਪ ਤਿਆਰ ਕਰੋ", "prepare_summary"),
    ("ur", "ڈاکٹر کے لیے خلاصہ تیار کریں", "prepare_summary"),
    ("te", "దయచేసి ఆపండి", "stop"),
    ("ml", "ദയവായി നിർത്തുക", "stop"),
    ("gu", "ફરીથી કહો", "repeat"),
    ("mr", "मला अजून सांगा", "more"),
]
NOISE = [("", ""), ("uh ", ""), ("", " please"), ("okay ", "")]


def misheard(w: str) -> str:
    """One letter swapped (the on-device matcher tolerates it; the server path need not)."""
    return w[:1] + w[2] + w[1] + w[3:] if len(w) > 4 else w


async def main() -> int:
    from app.llm.orchestrator import to_english

    rows, ok_cmd, n_cmd, false_q = [], 0, 0, 0
    for want, phrases in CONTROLS.items():
        for p in phrases:
            for pre, post in NOISE:
                t = f"{pre}{p}{post}".strip()
                a = parse(t)
                got = a.params.get("intent") if a and a.action == "control" else (a.action if a else None)
                good = got == want
                n_cmd += 1
                ok_cmd += good
                rows.append({"text": t, "want": want, "got": got, "ok": good})
    for want, phrases in ACTIONS.items():
        for p in phrases:
            a = parse(p)
            good = bool(a and a.action == want)
            n_cmd += 1
            ok_cmd += good
            rows.append({"text": p, "want": want, "got": a.action if a else None, "ok": good})
    for q in QUESTIONS:
        a = parse(q)
        bad = bool(a and a.action in ("control", "prepare_summary", "call_nearest", "memory_summary"))
        false_q += bad
        rows.append({"text": q, "want": None, "got": a.action if a else None, "ok": not bad})
    for lang, text, want in PIVOT:
        en = await to_english(text, lang)
        a = parse(en)
        got = a.params.get("intent") if a and a.action == "control" else (a.action if a else None)
        good = got == want
        n_cmd += 1
        ok_cmd += good
        rows.append({"text": text, "english": en, "want": want, "got": got, "ok": good, "lang": lang})
        print(f"  {lang}: {text} -> {en!r} -> {got}", flush=True)
    # the on-device matcher, through node (same table the app uses)
    misheard_cases = [(misheard(p), w) for w, ps in CONTROLS.items() for p in ps if len(p.split()) == 1 and len(p) > 4]
    js = r"""
const ts=require('typescript'),fs=require('fs'),vm=require('vm');
const js=ts.transpileModule(fs.readFileSync(process.argv[1],'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
const m={exports:{}};vm.runInNewContext(js,{module:m,exports:m.exports});
const cases=JSON.parse(fs.readFileSync(0,'utf8'));
console.log(JSON.stringify(cases.map(([t])=>m.exports.matchIntent(t))));"""
    web = ROOT / "apps" / "web"
    client = subprocess.run(["node", "-e", js, str(web / "lib" / "voiceIntents.ts")], cwd=web, capture_output=True,
                            text=True, encoding="utf-8",
                            input=json.dumps(misheard_cases + [(q, None) for q in QUESTIONS]))
    got = json.loads(client.stdout or "[]")
    client_ok = sum(1 for (t, w), g in zip(misheard_cases, got) if g == w or (w == "repeat" and g == "repeat"))
    client_false = sum(1 for g in got[len(misheard_cases):] if g)
    res = {"command_accuracy": round(ok_cmd / n_cmd, 3), "commands": n_cmd,
           "question_false_command_rate": round(false_q / len(QUESTIONS), 3), "questions": len(QUESTIONS),
           "client_misheard_accuracy": round(client_ok / max(1, len(misheard_cases)), 3),
           "client_questions_false": client_false, "total_utterances": len(rows) + len(misheard_cases)}
    res["pass"] = res["command_accuracy"] >= 0.95 and res["question_false_command_rate"] <= 0.01 and client_false == 0
    for r in rows:
        if not r["ok"]:
            print("MISS", r, flush=True)
    print(json.dumps(res, indent=2))
    (ROOT / "eval" / "reports" / "voice_intent.json").write_text(json.dumps({**res, "rows": rows}, ensure_ascii=False, indent=1), "utf-8")
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
