"""Pick the reranker score above which a retrieved passage is shown as "consulted".

Uses the 150 chat cases: a passage is relevant if its title+text matches the case's `terms` regex (same rule as
chat_eval). Prints precision / coverage for candidate thresholds; the chosen value goes in
orchestrator.CONSULT_MIN.

    cd services/api && .venv/Scripts/python -I ../../eval/consult_threshold.py
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))
COLLECTIONS = ["guidelines_in", "patient_ed", "drug_monographs", "nutrition_in"]


async def main() -> None:
    from app.db import SessionLocal
    from app.llm import orchestrator
    from app.rag.store import search

    cases = [json.loads(x) for x in (ROOT / "eval" / "chat_cases.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    pairs = []  # (score, relevant, case)
    async with SessionLocal() as db:
        for i, c in enumerate(cases, 1):
            english = await orchestrator.to_english(c["text"], c["lang"])
            hits = await search(db, english, collections=COLLECTIONS)
            for rank, h in enumerate(hits):
                pairs.append((h.score, bool(re.search(c["terms"], f"{h.title} {h.content}", re.I)), c["id"], rank))
            if i % 25 == 0:
                print(f"  {i}/{len(cases)} cases", flush=True)
    out = []
    for t in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7):
        shown = [p for p in pairs if p[0] >= t]
        prec = sum(p[1] for p in shown) / max(1, len(shown))
        covered = len({p[2] for p in shown}) / len(cases)
        out.append({"threshold": t, "precision": round(prec, 3), "cases_with_a_source": round(covered, 3)})
        print(f"  >= {t:.1f}: precision {prec:.0%}, questions with a source shown {covered:.0%}", flush=True)
    top = []  # the top-ranked hit alone, at lower thresholds
    for t in (0.01, 0.02, 0.03, 0.05, 0.07, 0.1):
        shown = [p for p in pairs if p[3] == 0 and p[0] >= t]
        prec = sum(p[1] for p in shown) / max(1, len(shown))
        top.append({"threshold": t, "precision": round(prec, 3), "cases_with_a_source": round(len(shown) / len(cases), 3)})
        print(f"  top hit >= {t:.2f}: precision {prec:.0%}, questions with a source shown {len(shown) / len(cases):.0%}", flush=True)
    ok = [r for r in out if r["precision"] >= 0.8]
    pick = max(ok, key=lambda r: r["cases_with_a_source"]) if ok else max(out, key=lambda r: r["precision"])
    print(f"Recommended CONSULT_MIN = {pick['threshold']}")
    (ROOT / "eval" / "reports" / "consult_threshold.json").write_text(json.dumps({"rows": out, "top_hit": top, "pick": pick}, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
