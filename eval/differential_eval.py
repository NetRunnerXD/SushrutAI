"""The chat's question round (app/triage/chat_flow.py) with the real local model and a simulated patient.

For each case the "patient" answers yes when a question asks about one of the true condition's symptoms and no
otherwise; at the "which way?" choice it says "tell me both now". Measured:
- questions asked before the answer (median <= 3, never more than 4 incl. the choice),
- the true condition is among the possibilities shown (top 3),
- the final urgency is never below the reference.
Run (GPU, one at a time): services/api/.venv/Scripts/python -I -X utf8 eval/differential_eval.py
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

RANK = {"self_care": 0, "see_doctor_week": 1, "see_doctor_24h": 2, "emergency": 3}
# complaint, true condition (and accepted names), its symptoms (for the simulated patient), reference urgency
CASES = [
    ("Fever and body ache for 3 days, pain behind the eyes", ["dengue"], ["eye", "joint", "muscle", "rash", "headache", "body"], "see_doctor_24h"),
    ("High fever with shivering that comes every other day", ["malaria"], ["chill", "shiver", "sweat", "alternate", "headache"], "see_doctor_24h"),
    ("Fever for 6 days, stomach pain and constipation", ["typhoid", "enteric"], ["stomach", "abdominal", "constipation", "appetite", "weak"], "see_doctor_24h"),
    ("Cough for 3 weeks with weight loss and night sweats", ["tuberculosis", "tb"], ["sweat", "weight", "blood", "appetite", "evening fever"], "see_doctor_24h"),
    ("Runny nose, sneezing and mild sore throat since yesterday", ["common cold", "viral", "upper respiratory", "rhinitis", "cold"], ["sneez", "runny", "throat", "nose"], "self_care"),
    ("Burning while passing urine and going often", ["urinary tract", "uti", "cystitis"], ["urine", "urinat", "burning", "lower abdom", "frequen"], "see_doctor_24h"),
    ("Loose motions 6 times since morning after eating outside", ["gastroenteritis", "diarrh", "food poison"], ["vomit", "stomach", "cramp", "outside", "water"], "see_doctor_24h"),
    ("Itchy red rash between the toes", ["fungal", "tinea", "athlete"], ["itch", "scal", "toe", "moist", "peel"], "see_doctor_week"),
    ("Headache on one side with nausea, light hurts my eyes", ["migraine"], ["light", "nause", "one side", "throbb", "sound"], "see_doctor_week"),
    ("Pain in the upper right belly after fatty food", ["gallstone", "biliary", "cholecyst"], ["fatty", "right", "nause", "shoulder", "after food"], "see_doctor_24h"),
    ("Tiredness, very thirsty and passing urine a lot", ["diabetes", "hyperglyc"], ["thirst", "urine", "weight", "tired", "hungry"], "see_doctor_week"),
    ("Sore throat with fever and white patches on the tonsils", ["tonsillitis", "pharyngitis", "strep"], ["swallow", "tonsil", "fever", "neck", "white"], "see_doctor_24h"),
]


def patient(question: str, symptoms: list[str]) -> str:
    q = question.lower()
    return "yes" if any(s in q for s in symptoms) else "no"


async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="sushrut-diff-"))
    os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{(tmp / 'diff.db').as_posix()}")
    os.environ.setdefault("STORAGE_DIR", str(tmp / "storage"))
    from app.db import SessionLocal, init_db
    from app.models import User
    from app.security.auth import CurrentUser
    from app.security.crypto import Cipher, new_wrapped_dek
    from app.triage import chat_flow

    await init_db()
    rows = []
    async with SessionLocal() as db:
        u = User(dek_wrapped=new_wrapped_dek(), is_guest=True)
        db.add(u)
        await db.commit()
        cu = CurrentUser(u, Cipher(u.dek_wrapped, u.id))
    for i, (complaint, names, symptoms, ref) in enumerate(CASES, 1):
        t0 = time.time()
        chat = f"eval-{i}"
        asked = []
        async with SessionLocal() as db:
            step = await chat_flow.start(db, cu, chat, complaint, "adult, age 35", 35, "en", "", allow_cloud=False)
        while step.kind == "ask" and len(asked) < 6:
            asked.append(step.reply)
            mode = (step.structured or {}).get("clarify", {}).get("mode")
            said = "tell me both now" if mode == "negotiate" else patient(step.reply, symptoms)
            async with SessionLocal() as db:
                step = await chat_flow.handle_reply(db, cu, chat, said, "", "en")
        if step.kind != "finish":
            rows.append({"case": complaint, "ok": False, "why": f"no answer ({step.kind})", "asked": len(asked)})
            print(f"FAIL {i:2} {complaint[:50]:50} no answer", flush=True)
            continue
        top = [r["name"].lower() for r in step.ranked]
        in_top = any(any(n in t for n in names) for t in top)
        urg_ok = RANK.get(step.urgency or "self_care", 0) >= RANK[ref]
        ok = in_top and urg_ok and len(asked) <= 4
        rows.append({"case": complaint, "ok": ok, "asked": len(asked), "top": top, "in_top3": in_top,
                     "urgency": step.urgency, "reference": ref, "urgency_ok": urg_ok, "seconds": round(time.time() - t0, 1),
                     "questions": asked})
        print(f"{'PASS' if ok else 'FAIL'} {i:2} {complaint[:50]:50} asked={len(asked)} top={top} urgency={step.urgency} "
              f"({'ok' if urg_ok else 'BELOW ' + ref}) {time.time() - t0:.0f}s", flush=True)
    done = [r for r in rows if "in_top3" in r]
    res = {"cases": len(rows), "answered": len(done),
           "median_questions": statistics.median([r["asked"] for r in rows]) if rows else None,
           "max_questions": max((r["asked"] for r in rows), default=None),
           "true_in_top3": round(sum(r["in_top3"] for r in done) / max(1, len(done)), 3),
           "urgency_never_below": all(r["urgency_ok"] for r in done) and len(done) == len(rows),
           "passed": sum(r["ok"] for r in rows)}
    print(json.dumps(res, indent=2))
    (ROOT / "eval" / "reports" / "differential.json").write_text(json.dumps({**res, "rows": rows}, ensure_ascii=False, indent=1, default=str), "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
