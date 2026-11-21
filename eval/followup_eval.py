"""Chained conversations: does a short follow-up keep the topic of the question before it?

10 two-turn cases (en / hi / Hinglish). Turn 1 is answered once; turn 2 (e.g. "how long will it last?") is answered
with follow-up retrieval off (old behaviour: search with the follow-up alone) and on (search with the previous
question's topic). Per case:
  topic_source   a retrieved source (top 3) is about the topic (regex on title + text)
  topic_answer   the answer mentions the topic
  urgency_kept   the follow-up answer is not less urgent than turn 1's (symptom chats)
    cd services/api && .venv/Scripts/python -I -X utf8 ../../eval/followup_eval.py
Writes eval/reports/followup.json. One model run at a time (turn 1, then turn 2 off, then turn 2 on).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

CASES = [
    {"id": "dengue-en", "lang": "en", "t1": "I think I have dengue, high fever and body pain for 2 days.",
     "t2": "How long will it last?", "topic": r"dengue|डेंगू"},
    {"id": "typhoid-en", "lang": "en", "t1": "My doctor said I might have typhoid.", "t2": "What should I eat?",
     "topic": r"typhoid|टाइफाइड"},
    {"id": "migraine-en", "lang": "en", "t1": "I get migraines twice a month.", "t2": "What triggers it?",
     "topic": r"migraine|माइग्रेन"},
    {"id": "acidity-en", "lang": "en", "t1": "I have acidity and burning in my chest after meals.", "t2": "Is it serious?",
     "topic": r"acid|heartburn|gerd|reflux|जलन|एसिडिटी"},
    {"id": "diabetes-en", "lang": "en", "t1": "I was just told I have type 2 diabetes.", "t2": "Can it be reversed?",
     "topic": r"diabet|sugar|शुगर|मधुमेह"},
    {"id": "jaundice-hi", "lang": "hi", "t1": "मेरे भाई को पीलिया हो गया है।", "t2": "कितने दिन में ठीक होगा?",
     "topic": r"jaundice|hepatitis|पीलिया|हेपेटाइटिस|liver|लिवर"},
    {"id": "asthma-hi", "lang": "hi", "t1": "मेरी बेटी को दमा है।", "t2": "क्या वह खेल सकती है?",
     "topic": r"asthma|दमा|अस्थमा"},
    {"id": "pregnancy-hinglish", "lang": "en", "t1": "main 3 mahine pregnant hu, ulti bahut hoti hai", "t2": "kya ye normal hai?",
     "topic": r"pregnan|nausea|vomit|morning sickness|गर्भ|उल्टी|ulti"},
    {"id": "uti-hinglish", "lang": "en", "t1": "peshab mein jalan ho rahi hai 2 din se", "t2": "aur kya karu?",
     "topic": r"urin|uti|bladder|पेशाब|peshab|infection"},
    {"id": "thyroid-en", "lang": "en", "t1": "My TSH came back high at 9.", "t2": "What does that mean for my weight?",
     "topic": r"thyroid|tsh|hypothyroid|थायरॉइड"},
]
LEVEL = {"none": 0, "self_care": 1, "see_doctor_week": 2, "see_doctor_24h": 3, "emergency": 4}


def urgency(r) -> str:
    if r.red_flag and r.red_flag.get("triggered"):
        return "emergency" if r.red_flag.get("kind") == "emergency" else "see_doctor_24h"
    pcs = (r.structured or {}).get("possible_conditions") or []
    return max((p.get("urgency", "none") for p in pcs), key=lambda u: LEVEL.get(u, 0), default="none")


async def run() -> dict:
    from app.db import SessionLocal
    from app.llm import orchestrator
    from app.models import User
    from app.rag.store import search
    from app.security.auth import CurrentUser
    from app.security.crypto import Cipher, new_wrapped_dek

    rows = {c["id"]: {"id": c["id"]} for c in CASES}
    async with SessionLocal() as db:
        u = User(dek_wrapped=new_wrapped_dek(), is_guest=True)
        db.add(u)
        await db.commit()
        cu = CurrentUser(u, Cipher(u.dek_wrapped, u.id))
        try:
            first = {}
            for c in CASES:
                r1 = await orchestrator.answer(db, cu, c["t1"], lang=c["lang"], model_pref="balanced")
                first[c["id"]] = [{"role": "user", "content": c["t1"]},
                                  {"role": "assistant", "content": r1.reply, "structured": r1.structured}]
                rows[c["id"]]["t1_urgency"] = urgency(r1)
                print(f"turn1 {c['id']:20} {urgency(r1)}", flush=True)
            for mode in ("off", "on"):
                os.environ["FOLLOWUP_CONTEXT"] = mode
                for c in CASES:
                    hist = first[c["id"]]
                    english = await orchestrator.to_english(c["t2"], c["lang"])
                    hits = await search(db, orchestrator.retrieval_query(english, hist),
                                        collections=["guidelines_in", "patient_ed", "drug_monographs", "nutrition_in"])
                    r2 = await orchestrator.answer(db, cu, c["t2"], lang=c["lang"], history=hist, model_pref="balanced")
                    topic = re.compile(c["topic"], re.I)
                    text = (r2.reply or "") + " " + json.dumps(r2.structured or {}, ensure_ascii=False)
                    res = {"topic_source": any(topic.search(f"{h.title} {h.content}") for h in hits[:3]),
                           "topic_answer": bool(topic.search(text)),
                           "urgency": urgency(r2),
                           "urgency_kept": LEVEL.get(urgency(r2), 0) >= LEVEL.get(rows[c["id"]]["t1_urgency"], 0) - 1,
                           "top": [h.title[:50] for h in hits[:3]], "reply": (r2.reply or "")[:300]}
                    rows[c["id"]][mode] = res
                    print(f"{mode:3} {c['id']:20} src={res['topic_source']!s:5} ans={res['topic_answer']!s:5} "
                          f"urg={res['urgency']} | {res['top'][0] if res['top'] else '-'}", flush=True)
        finally:
            os.environ.pop("FOLLOWUP_CONTEXT", None)
            await db.delete(u)
            await db.commit()
    summ = {}
    for mode in ("off", "on"):
        rs = [r[mode] for r in rows.values() if mode in r]
        n = len(rs) or 1
        summ[mode] = {k: round(100 * sum(r[k] for r in rs) / n, 1) for k in ("topic_source", "topic_answer", "urgency_kept")}
    return {"summary": summ, "rows": list(rows.values())}


if __name__ == "__main__":
    rep = asyncio.run(run())
    out = ROOT / "eval" / "reports" / "followup.json"
    out.write_text(json.dumps(rep, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(rep["summary"], indent=1))
    print(f"Report: {out}")
