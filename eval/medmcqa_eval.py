"""MedMCQA accuracy of a local model (Indian AIIMS/NEET-PG entrance questions, validation split, 4,183 items).

    cd services/api && .venv/Scripts/python ../../eval/medmcqa_eval.py --n 200 --model medgemma:4b

A fixed random sample (seed) keeps runs comparable across models. Random guessing scores 25%.
Dataset: openlifescienceai/medmcqa (Pal et al., CHIL 2022). Answer key `cop` is 0-3 (A-D).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

SCHEMA = {"type": "object", "properties": {"answer": {"type": "string", "enum": ["A", "B", "C", "D"]}},
          "required": ["answer"]}


async def run(n: int, model: str, seed: int) -> dict:
    import pyarrow.parquet as pq

    from app.llm.providers import ProviderUnavailable, ollama

    rows = pq.read_table(ROOT / "data" / "eval" / "medmcqa" / "validation.parquet").to_pylist()
    random.Random(seed).shuffle(rows)
    rows = rows[:n]
    correct, by_subj, errors, t0 = 0, defaultdict(lambda: [0, 0]), 0, time.time()
    for i, r in enumerate(rows, 1):
        q = (f"{r['question']}\nA. {r['opa']}\nB. {r['opb']}\nC. {r['opc']}\nD. {r['opd']}\n"
             "Answer with the single best option letter.")
        try:
            res = await ollama().chat(model, [{"role": "user", "content": q}], schema=SCHEMA, temperature=0.0,
                                      num_ctx=2048, num_predict=12)
            ans = res.json().get("answer", "")
        except (ProviderUnavailable, ValueError):
            ans, errors = "", errors + 1
        ok = ans == "ABCD"[int(r["cop"])]
        correct += ok
        s = by_subj[r["subject_name"]]
        s[0] += ok
        s[1] += 1
        if i % 25 == 0:
            print(f"  {i}/{n}: {100 * correct / i:.1f}%", flush=True)
    return {"model": model, "n": n, "seed": seed, "accuracy_pct": round(100 * correct / n, 1), "errors": errors,
            "seconds": round(time.time() - t0), "by_subject": {k: f"{v[0]}/{v[1]}" for k, v in
                                                               sorted(by_subj.items(), key=lambda x: -x[1][1])}}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--model", default="medgemma:4b")
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    rep = asyncio.run(run(a.n, a.model, a.seed))
    out = ROOT / "eval" / "reports"
    out.mkdir(exist_ok=True)
    p = out / f"medmcqa_{a.model.replace(':', '-')}_{a.n}_{time.strftime('%Y%m%d-%H%M%S')}.json"
    p.write_text(json.dumps(rep, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in rep.items() if k != "by_subject"}, indent=2))
    print(f"Report: {p}")


if __name__ == "__main__":
    main()
