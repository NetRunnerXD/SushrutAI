"""Speed of the answer-speed modes on an idle machine (nothing else on the GPU or CPU).

  chat: the same 8 non-symptom questions answered in Careful (balanced), Faster (fast) and Instant; per-stage
        timings from orchestrator.answer. One warm-up per mode so model loading is not counted.
  labs: 5 lab-report pages read by the default (qwen3-vl:4b) and the Faster/Instant reader (qwen3-vl:2b).

    cd services/api && .venv/Scripts/python -I ../../eval/speed_eval.py
Writes eval/reports/speed_modes.json.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))
sys.path.insert(0, str(ROOT / "eval"))

QUESTIONS = [
    ("What is a healthy breakfast for a diabetic?", "en"), ("Is Dolo 650 safe with BP tablets?", "en"),
    ("How much water should I drink in summer?", "en"), ("What foods are rich in iron?", "en"),
    ("How can I sleep better at night?", "en"), ("Is it okay to walk after dinner?", "en"),
    ("What does HbA1c mean in a report?", "en"), ("roz kitna namak khana chahiye?", "hi"),
]


async def chat_speeds() -> dict:
    from app.db import SessionLocal
    from app.llm import orchestrator
    from app.models import User
    from app.security.auth import CurrentUser
    from app.security.crypto import Cipher, new_wrapped_dek

    out = {}
    async with SessionLocal() as db:
        u = User(dek_wrapped=new_wrapped_dek(), is_guest=True)
        db.add(u)
        await db.commit()
        cu = CurrentUser(u, Cipher(u.dek_wrapped, u.id))
        try:
            for mode in ("balanced", "fast", "instant"):
                await orchestrator.answer(db, cu, "What is a balanced diet?", lang="en", model_pref=mode)  # warm-up
                times, stages, models = [], {}, set()
                for q, lang in QUESTIONS:
                    t0 = time.perf_counter()
                    r = await orchestrator.answer(db, cu, q, lang=lang, model_pref=mode)
                    times.append(time.perf_counter() - t0)
                    models.add(r.model)
                    for k, v in (r.timings or {}).items():
                        stages.setdefault(k, []).append(v)
                out[mode] = {"median_s": round(statistics.median(times), 1), "max_s": round(max(times), 1),
                             "models": sorted(m for m in models if m),
                             "stage_median_s": {k: round(statistics.median(v), 2) for k, v in stages.items()}}
                print(mode, json.dumps(out[mode]), flush=True)
        finally:
            await db.delete(u)
            await db.commit()
    return out


async def lab_speeds() -> dict:
    import pyarrow.parquet as pq

    from app.llm.providers import ollama
    from app.ocr.pipeline import load_image, preprocess
    from app.routers.reports import LAB_PROMPT
    from doc_type_eval import truth_label

    rows = [r for r in pq.read_table(ROOT / "data/eval/labs_medocr/test.parquet").to_pylist()
            if truth_label(r["text"] or "") == "lab_report"][:5]
    pages = []
    for r in rows:
        img = preprocess(load_image(r["image"]["bytes"], "image/png"), max_side=1600)
        b = io.BytesIO()
        img.save(b, format="JPEG", quality=90)
        pages.append(base64.b64encode(b.getvalue()).decode())
    out = {}
    for model in ("qwen3-vl:4b-instruct", "qwen3-vl:2b-instruct"):
        await ollama().chat(model, [{"role": "user", "content": LAB_PROMPT}], images=[pages[0]], temperature=0.0,
                            num_ctx=6144, num_predict=50)  # warm-up / load
        ts = []
        for b64 in pages:
            t0 = time.perf_counter()
            await ollama().chat(model, [{"role": "user", "content": LAB_PROMPT}], images=[b64], temperature=0.0,
                                num_ctx=6144, num_predict=1500)
            ts.append(time.perf_counter() - t0)
        out[model] = {"median_s": round(statistics.median(ts), 1), "max_s": round(max(ts), 1)}
        print(model, out[model], flush=True)
    return out


if __name__ == "__main__":
    rep = {"chat": asyncio.run(chat_speeds()), "labs": asyncio.run(lab_speeds()),
           "note": "Idle machine, warm models, RTX 3060 6 GB. Symptom questions always use the Careful model."}
    (ROOT / "eval" / "reports" / "speed_modes.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    print(json.dumps(rep, indent=1))
