"""Chat intent evaluation.

1. Routing accuracy of app.llm.intent.classify over eval/intent_cases.jsonl (80 labelled messages: English,
   Hindi, Hinglish and other Indian scripts). Uses the live bge-m3 embeddings for the fallback layer.
2. End to end (--e2e N): sends N non-symptom messages through the real assistant pipeline and counts how many
   answers still show possible diseases, tests or a specialist. Target: 0.

    cd services/api && .venv/Scripts/python ../../eval/intent_eval.py
    .venv/Scripts/python ../../eval/intent_eval.py --e2e 12
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))


async def main(e2e: int) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="sushrut-intent-"))
    os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{(tmp / 'intent.db').as_posix()}")
    os.environ.setdefault("STORAGE_DIR", str(tmp / "storage"))
    from app.db import SessionLocal, init_db
    from app.llm import orchestrator
    from app.llm.intent import classify
    from app.models import User
    from app.security.auth import CurrentUser
    from app.security.crypto import Cipher, new_wrapped_dek

    await init_db()
    cases = [json.loads(x) for x in (ROOT / "eval" / "intent_cases.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    wrong, confusion = [], Counter()
    async with SessionLocal() as db:
        for c in cases:
            got = await classify(db, c["text"])
            confusion[(c["intent"], got)] += 1
            if got != c["intent"]:
                wrong.append((c["text"], c["intent"], got))
    correct = len(cases) - len(wrong)
    print(f"Routing accuracy: {correct}/{len(cases)} = {correct / len(cases):.1%}")
    # what matters most for the bug: a non-symptom message routed to the symptom (diagnosis) path, and vice versa
    to_dx = [w for w in wrong if w[2] == "symptoms"]
    missed_dx = [w for w in wrong if w[1] == "symptoms"]
    print(f"Non-symptom messages sent to the diagnosis path: {len(to_dx)}")
    print(f"Symptom messages NOT sent to the diagnosis path: {len(missed_dx)}")
    for t, exp, got in wrong:
        print(f"  MISS  {t!r}: expected {exp}, got {got}")

    if e2e:
        # round-robin across intents so the model-backed kinds (food, medicine, report, general, off-topic) are covered
        by_kind: dict[str, list[dict]] = {}
        for c in cases:
            if c["intent"] != "symptoms":
                by_kind.setdefault(c["intent"], []).append(c)
        order = ["nutrition_lifestyle", "medicine", "report_question", "general_health", "off_topic", "greeting",
                 "thanks_bye", "about_app"]
        non_symptom, i = [], 0
        while len(non_symptom) < e2e and any(len(by_kind.get(k, [])) > i for k in order):
            non_symptom += [by_kind[k][i] for k in order if len(by_kind.get(k, [])) > i]
            i += 1
        non_symptom = non_symptom[:e2e]
        shown = 0
        async with SessionLocal() as db:
            u = User(dek_wrapped=new_wrapped_dek(), is_guest=True)
            db.add(u)
            await db.commit()
            cu = CurrentUser(u, Cipher(u.dek_wrapped, u.id))
            for c in non_symptom:
                t0 = time.time()
                r = await orchestrator.answer(db, cu, c["text"], lang="hi" if any("ऀ" <= ch <= "ॿ" for ch in c["text"]) else "en")
                s = r.structured or {}
                dx = bool(s.get("possible_conditions") or s.get("suggested_tests") or s.get("specialist"))
                shown += dx
                print(f"  {'DX! ' if dx else 'ok  '} {time.time() - t0:5.1f}s  {s.get('kind')!s:20} {c['text']!r} -> {(r.reply or '')[:90]!r}")
        print(f"End to end: diagnosis shown for {shown}/{len(non_symptom)} non-symptom messages (target 0)")
    out = ROOT / "eval" / "reports" / "intent_eval.json"
    out.write_text(json.dumps({"accuracy": correct / len(cases), "n": len(cases), "wrong": wrong,
                               "non_symptom_to_dx": len(to_dx), "symptom_missed": len(missed_dx)},
                              ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--e2e", type=int, default=0, help="also run N non-symptom messages end to end (needs Ollama)")
    asyncio.run(main(ap.parse_args().e2e))
