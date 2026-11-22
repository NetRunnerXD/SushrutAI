"""Suggestion chips: rules only vs a tiny CPU model (qwen3:0.6b, gemma3:270m) picking/rephrasing them.

40 contexts (English / Hindi; no data, chronic conditions, abnormal results, high BP, pregnancy care, refills,
and the last chat turn: fever, acidity, child cough, knee pain, period pain, a food question, a report question).
Per variant:
  relevance      mean reranker score (bge-reranker-v2-m3) of each chip against the context description
  personal       contexts with personal data where >= 1 chip mentions it (condition, result, BP, last answer topic)
  lang_ok        chips in the person's language (Hindi chips in Devanagari)
  medicines      chips naming a medicine (target 0)
  variety        distinct chips / all chips across contexts (static lists score low)
  p50/p95 ms     latency on the CPU (model warmed first); within_budget = share under the 1.5 s app timeout
  fallback       contexts where the model failed/was too slow and rules were used

    cd services/api && .venv/Scripts/python -I ../../eval/suggest_eval.py [--models qwen3:0.6b,gemma3:270m]
Writes eval/reports/suggest.json. Runs one variant at a time (CPU only: never competes with MedGemma for the GPU).
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import re
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

PROFILES = {
    "none": {},
    "diabetes": {"age": 52, "conditions": ["Type 2 diabetes"], "has_meds": True, "abnormal_labs": ["HbA1c"]},
    "bp": {"age": 61, "conditions": ["Hypertension"], "has_meds": True, "bp": "150/95", "refill_due": True},
    "pregnant": {"age": 27, "care_due": ["Tetanus-diphtheria (Td) dose 2"]},
    "thyroid": {"age": 38, "conditions": ["Hypothyroidism"], "abnormal_labs": ["TSH"], "has_meds": True},
}
TURNS = {
    "none": {},
    "fever": {"last_question": "I have fever and body ache for 2 days", "last_kind": "symptoms",
              "last_conditions": ["Viral fever", "Dengue"], "last_watch": True,
              "last_answer": "This can match a viral fever; dengue is possible in this season. Rest, drink fluids and get a dengue test if the fever continues."},
    "acidity": {"last_question": "burning in chest after meals", "last_kind": "symptoms",
                "last_conditions": ["Acidity (GERD)"], "last_watch": True,
                "last_answer": "This can match acid reflux (GERD). Eat smaller meals and do not lie down for 2 hours after eating."},
    "food": {"last_question": "what should I eat for lunch with diabetes", "last_kind": "nutrition_lifestyle",
             "last_answer": "A plate with half vegetables, some dal or paneer and a small portion of roti or millet keeps sugar steady."},
    "report": {"last_question": "explain my HbA1c 8.1", "last_kind": "report_question",
               "last_answer": "An HbA1c of 8.1% means your average sugar over 3 months is above the target of 7%. Your doctor may review your treatment."},
}
TURNS_HI = {
    "fever": {"last_question": "दो दिन से बुखार और बदन दर्द है", "last_conditions": ["वायरल बुखार", "डेंगू"],
              "last_answer": "यह वायरल बुखार हो सकता है; इस मौसम में डेंगू भी संभव है। आराम करें, पानी पिएँ।"},
    "acidity": {"last_question": "खाने के बाद सीने में जलन", "last_conditions": ["एसिडिटी"],
                "last_answer": "यह एसिडिटी हो सकती है। थोड़ा-थोड़ा खाएँ और खाने के बाद तुरंत न लेटें।"},
    "food": {"last_question": "डायबिटीज़ में दोपहर में क्या खाएँ",
             "last_answer": "आधी थाली सब्ज़ी, थोड़ी दाल और एक-दो रोटी से शुगर ठीक रहती है।"},
    "report": {"last_question": "मेरी HbA1c 8.1 का मतलब समझाइए",
               "last_answer": "HbA1c 8.1% का मतलब है पिछले 3 महीने की औसत शुगर लक्ष्य 7% से ज़्यादा है।"},
}
NAMES_HI = {"Type 2 diabetes": "टाइप 2 डायबिटीज़", "Hypertension": "हाई BP", "Hypothyroidism": "थायरॉइड की कमी"}


def contexts():
    from app.llm.suggest import Ctx

    out = []
    for (pk, p), (tk, tt), lang in itertools.product(PROFILES.items(), TURNS.items(), ("en", "hi")):
        if (pk, tk) in {("pregnant", "report"), ("thyroid", "food"), ("none", "report")}:
            continue
        t = dict(tt)
        if lang == "hi" and tk in TURNS_HI:
            t.update(TURNS_HI[tk])
        prof = dict(p)
        if lang == "hi":
            prof["conditions"] = [NAMES_HI.get(x, x) for x in prof.get("conditions", [])]
        c = Ctx(lang=lang, season="winter", hour=9 if len(out) % 2 else 21, day=738000 + len(out), **prof, **t)
        out.append((f"{pk}/{tk}/{lang}", c))
    return out[:40]


def personal_terms(c) -> list[str]:
    terms = [*c.conditions, *c.abnormal_labs, *c.last_conditions, *c.care_due] + ([c.bp, "BP"] if c.bp else [])
    return [w for x in terms for w in re.findall(r"[\wऀ-ॿ]{3,}", x)]


async def run(models: list[str]) -> dict:
    from app.db import SessionLocal
    from app.llm.orchestrator import _medicine_names
    from app.llm.suggest import model_pick, suggest
    from app.rag import rerank

    async with SessionLocal() as db:
        med_rx = await _medicine_names(db)
    ctxs = contexts()
    print(f"{len(ctxs)} contexts; variants: rules, {', '.join(models)}", flush=True)
    report = {}
    for variant in ["rules", *models]:
        model = None if variant == "rules" else variant
        if model:  # warm the model on the CPU so load time is not counted
            t0 = time.perf_counter()
            await model_pick(ctxs[0][1], [], model, timeout=120)
            await model_pick(next(c for _, c in ctxs if c.lang == "hi"), [], model, timeout=120)
            print(f"[{variant}] loaded in {time.perf_counter() - t0:.1f}s", flush=True)
        rows = []
        for cid, c in ctxs:
            r = await suggest(c, model, med_rx, timeout=8.0)
            items = r["items"]
            scores = await asyncio.to_thread(rerank.score, c.describe(), items) if items else []
            terms = personal_terms(c)
            personal = any(t.lower() in x.lower() for x in items for t in terms) if terms else None
            lang_ok = all(bool(re.search(r"[ऀ-ॿ]", x)) == (c.lang == "hi") for x in items)
            meds = [m.group(0) for x in items for m in med_rx.finditer(x)]
            rows.append({"id": cid, "items": items, "source": r["source"], "ms": r["ms"],
                         "relevance": round(statistics.mean(scores), 3) if scores else 0.0,
                         "personal": personal, "lang_ok": lang_ok, "medicines": meds})
            print(f"[{variant:12}] {cid:24} {r['source']:5} {r['ms']:5}ms rel={rows[-1]['relevance']:.2f} "
                  f"pers={personal} | {' | '.join(items)}", flush=True)
        ms = sorted(x["ms"] for x in rows)
        pers = [x["personal"] for x in rows if x["personal"] is not None]
        allchips = [i for x in rows for i in x["items"]]
        report[variant] = {
            "relevance": round(statistics.mean(x["relevance"] for x in rows), 3),
            "personal_pct": round(100 * sum(pers) / len(pers), 1) if pers else None,
            "lang_ok_pct": round(100 * sum(x["lang_ok"] for x in rows) / len(rows), 1),
            "medicine_chips": sum(len(x["medicines"]) for x in rows),
            "variety": round(len(set(allchips)) / max(1, len(allchips)), 3),
            "p50_ms": ms[len(ms) // 2], "p95_ms": ms[int(len(ms) * 0.95) - 1],
            "within_budget_pct": round(100 * sum(m <= 2000 for m in ms) / len(ms), 1),
            "within_3s_pct": round(100 * sum(m <= 3000 for m in ms) / len(ms), 1),
            "fallback_pct": round(100 * sum(x["source"] == "rules" for x in rows) / len(rows), 1) if model else None,
            "rows": rows}
        s = {k: v for k, v in report[variant].items() if k != "rows"}
        print(f"== {variant}: {json.dumps(s)}", flush=True)
    await judge(report, dict(ctxs))
    return report


JUDGE = ("You rate suggested follow-up questions in a patient health app. Conversation context: {ctx}\n"
         "Suggested questions:\n{items}\n\nFor EACH question give a score 1-5: 5 = a natural, useful question THIS "
         "patient would likely ask next, specific to this conversation and their data, correct language ({lang}); "
         "1 = generic, irrelevant, odd, wrong language, or not a question. Reply JSON {{\"scores\": [numbers]}}.")


async def judge(report: dict, ctxs: dict) -> None:
    """llama3.2:3b (the app's Faster model) scores each chip 1-5; run once all candidates are done (one model at a time)."""
    from app.config import get_settings
    import httpx

    url = get_settings().ollama_base_url.rstrip("/") + "/api/chat"
    schema = {"type": "object", "properties": {"scores": {"type": "array", "items": {"type": "integer"}}}, "required": ["scores"]}
    async with httpx.AsyncClient(timeout=120) as cl:
        for variant, rep in report.items():
            vals = []
            for row in rep["rows"]:
                c = ctxs[row["id"]]
                ctx = c.describe() + (f'; the app answered: "{c.last_answer}"' if c.last_answer else "")
                body = {"model": "llama3.2:3b", "stream": False, "format": schema, "options": {"temperature": 0},
                        "messages": [{"role": "user", "content": JUDGE.format(ctx=ctx, lang="Hindi" if c.lang == "hi" else "English",
                                                                               items="\n".join(f"{i + 1}. {x}" for i, x in enumerate(row["items"])))}]}
                try:
                    r = await cl.post(url, json=body)
                    sc = [max(1, min(5, int(x))) for x in json.loads(r.json()["message"]["content"])["scores"]][: len(row["items"])]
                except Exception:  # noqa: BLE001
                    sc = []
                row["judge"] = sc
                vals += sc
            rep["judge_mean"] = round(statistics.mean(vals), 2) if vals else None
            rep["judge_4plus_pct"] = round(100 * sum(v >= 4 for v in vals) / len(vals), 1) if vals else None
            print(f"== judge {variant}: mean {rep['judge_mean']} | >=4: {rep['judge_4plus_pct']}%", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="gemma3:1b,qwen3:1.7b,qwen2.5:0.5b")
    ap.add_argument("--tag", default="", help="store variants under '<variant>_<tag>' and keep earlier results")
    a = ap.parse_args()
    rep = asyncio.run(run([m for m in a.models.split(",") if m]))
    out = ROOT / "eval" / "reports" / "suggest.json"
    if a.tag:
        old = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
        rep = {**old, **{f"{k}_{a.tag}": v for k, v in rep.items()}}
    out.write_text(json.dumps(rep, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "rows"} for k, v in rep.items()}, indent=1))
    print(f"Report: {out}")
