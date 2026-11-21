"""Chat evaluation over eval/chat_cases.jsonl (150 cases: en / hi / Hinglish).

Retrieval (no LLM answer; uses bge-m3 + the reranker if enabled):
  hit@1, hit@6 (a relevant chunk in the top 1 / 6), MRR@6, precision@6, search latency p50/p95.
  A chunk is relevant if its title+text matches the case's `terms` regex.
Answers (--answers N, full pipeline through MedGemma, slow):
  urgency_ok (no under-triage vs the expected minimum), over_triage, required-fact recall, banned-advice
  violations, citation precision (cited sources that are relevant), grounding rate, latency p50/p95.

    cd services/api
    # before: old DB snapshot, no reranker, no grounding check
    DATABASE_URL=sqlite+aiosqlite:///<snapshot.db> RERANKER=off GROUNDING=off \
        .venv/Scripts/python ../../eval/chat_eval.py run --tag before --answers 30
    .venv/Scripts/python ../../eval/chat_eval.py run --tag after --answers 30
    .venv/Scripts/python ../../eval/chat_eval.py compare eval/reports/chat_before.json eval/reports/chat_after.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))
LEVEL = {"none": 0, "self_care": 1, "see_doctor_week": 2, "see_doctor_24h": 3, "emergency": 4}
_NEG = re.compile(r"(?i)(not|n't|never|avoid|don't|do not|no need|नहीं|मत|mat|nahi|बचें)[^.]{0,40}$")
COLLECTIONS = ["guidelines_in", "patient_ed", "drug_monographs", "nutrition_in"]


def _pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))], 2)


def _unneg(pat: str, text: str) -> str | None:
    for m in re.finditer(pat, text, re.I):
        if not _NEG.search(text[max(0, m.start() - 60):m.start()]):
            return m.group(0)
    return None


def observed_urgency(r) -> str:
    rf = r.red_flag or {}
    if rf.get("triggered") and rf.get("kind") in ("emergency", "mental_health"):
        return "emergency"
    levels = [pc.get("urgency", "self_care") for pc in (r.structured or {}).get("possible_conditions", []) or []]
    best = max(levels, key=lambda u: LEVEL.get(u, 0)) if levels else "none"
    if rf.get("triggered") and rf.get("kind") == "urgent":
        best = max(best, "see_doctor_24h", key=lambda u: LEVEL.get(u, 0))
    return best


async def run(tag: str, answers: int, only: str | None) -> dict:
    from app.db import SessionLocal, init_db
    from app.llm import orchestrator
    from app.models import User
    from app.rag import rerank
    from app.rag.store import search
    from app.security.auth import CurrentUser
    from app.security.crypto import Cipher, new_wrapped_dek

    await init_db()
    cases = [json.loads(x) for x in (ROOT / "eval" / "chat_cases.jsonl").read_text(encoding="utf-8").splitlines()
             if x.strip()]
    if only:
        cases = [c for c in cases if re.search(only, c["id"])]
    rows = []
    async with SessionLocal() as db:
        from sqlalchemy import func, select

        from app.models import KbChunk
        kb = dict((await db.execute(select(KbChunk.collection, func.count()).group_by(KbChunk.collection))).all())
        print(f"[{tag}] KB: {kb}  reranker={'on' if rerank.available() else 'off'}  "
              f"grounding={os.environ.get('GROUNDING', 'on')}", flush=True)
        for c in cases:  # retrieval
            english = await orchestrator.to_english(c["text"], c["lang"])
            t0 = time.perf_counter()
            hits = await search(db, english, collections=COLLECTIONS)
            dt = time.perf_counter() - t0
            rel = [bool(re.search(c["terms"], f"{h.title} {h.content}", re.I)) for h in hits]
            first = next((i for i, x in enumerate(rel) if x), None)
            rows.append({"id": c["id"], "variant": c["variant"], "search_s": round(dt, 3),
                         "hit1": bool(rel and rel[0]), "hit6": any(rel), "rr": 0 if first is None else 1 / (first + 1),
                         "p6": round(sum(rel) / max(1, len(rel)), 3),
                         "top": [f"{h.source_id.split(':')[0]}|{h.title[:60]}" for h in hits[:3]]})
        print(f"[{tag}] retrieval done", flush=True)

        if answers:
            u = User(dek_wrapped=new_wrapped_dek(), is_guest=True)
            db.add(u)
            await db.commit()
            cu = CurrentUser(u, Cipher(u.dek_wrapped, u.id))
            # spread across topics and languages: every 3rd case rotates language
            picked = [c for i, c in enumerate(cases) if i % 3 == (i // 3) % 3][:answers]
            by_id = {r["id"]: r for r in rows}
            for c in picked:
                t0 = time.perf_counter()
                try:
                    r = await orchestrator.answer(db, cu, c["text"], lang=c["lang"])
                except Exception as e:  # noqa: BLE001
                    by_id[c["id"]]["answer_error"] = str(e)[:200]
                    continue
                dt = time.perf_counter() - t0
                text = (r.reply or "") + " " + json.dumps(r.structured or {}, ensure_ascii=False)
                if r.red_flag and r.red_flag.get("messages"):
                    text += " " + " ".join(r.red_flag["messages"]) + " " + " ".join(r.red_flag.get("call_numbers") or [])
                obs = observed_urgency(r)
                req = [bool(re.search(p, text, re.I)) for p in c["required"]]
                ban = [b for b in (_unneg(p, text) for p in c["banned"]) if b]
                cites = r.citations or []
                g = (r.structured or {}).get("grounding") or {}
                by_id[c["id"]].update({
                    "answer_s": round(dt, 1), "urgency_expected": c["urgency"], "urgency_observed": obs,
                    "urgency_ok": LEVEL.get(obs, 0) >= LEVEL[c["urgency"]],
                    "over_triage": LEVEL.get(obs, 0) - LEVEL[c["urgency"]] >= 2,
                    "required_hit": sum(req), "required_n": len(req), "banned": ban,
                    "cited": len(cites),
                    "cited_relevant": sum(bool(re.search(c["terms"], x.get("title") or "", re.I)) or
                                          bool(re.search(c["terms"], x.get("source") or "", re.I)) for x in cites),
                    "grounding_rate": g.get("rate"), "grounding_flag": g.get("flag"),
                    "timings": getattr(r, "timings", {}), "model": r.model,
                    "reply": (r.reply or "")[:400]})
                print(f"  {c['id']:28} urg {obs:15} (>= {c['urgency']:15}) req {sum(req)}/{len(req)} "
                      f"ban {len(ban)} {dt:.0f}s", flush=True)
    return {"tag": tag, "kb": kb, "reranker": rerank.available(), "grounding": os.environ.get("GROUNDING", "on"),
            "summary": summarise(rows), "rows": rows}


def summarise(rows: list[dict]) -> dict:
    def mean(k, rs):
        v = [float(r[k]) for r in rs if r.get(k) is not None]
        return round(sum(v) / len(v), 3) if v else None

    s: dict = {"retrieval": {}, "answers": {}}
    for name, rs in [("all", rows)] + [(v, [r for r in rows if r["variant"] == v]) for v in ("en", "hi", "hinglish")]:
        s["retrieval"][name] = {"n": len(rs), "hit@1": mean("hit1", rs), "hit@6": mean("hit6", rs),
                                "mrr@6": mean("rr", rs), "precision@6": mean("p6", rs)}
    lat = [r["search_s"] for r in rows]
    s["retrieval"]["latency_s"] = {"p50": _pct(lat, .5), "p95": _pct(lat, .95)}
    ans = [r for r in rows if "urgency_ok" in r]
    if ans:
        cited = sum(r["cited"] for r in ans)
        g = [r["grounding_rate"] for r in ans if r.get("grounding_rate") is not None]
        s["answers"] = {
            "n": len(ans), "errors": sum("answer_error" in r for r in rows),
            "urgency_ok": f"{sum(r['urgency_ok'] for r in ans)}/{len(ans)}",
            "over_triage": sum(r["over_triage"] for r in ans),
            "required_recall": round(sum(r["required_hit"] for r in ans) / max(1, sum(r["required_n"] for r in ans)), 3),
            "banned_violations": sum(len(r["banned"]) for r in ans),
            "citation_precision": round(sum(r["cited_relevant"] for r in ans) / cited, 3) if cited else None,
            "answers_with_citations": sum(r["cited"] > 0 for r in ans),
            "grounding_rate": round(sum(g) / len(g), 3) if g else None,
            "flagged_not_in_sources": sum(r.get("grounding_flag") == "not_in_sources" for r in ans),
            "latency_s": {"p50": _pct([r["answer_s"] for r in ans], .5), "p95": _pct([r["answer_s"] for r in ans], .95)},
            # where the time goes: median seconds per stage (orchestrator.answer timings)
            "stage_p50_s": {k: _pct([r["timings"][k] for r in ans if k in (r.get("timings") or {})], .5)
                            for k in sorted({k for r in ans for k in (r.get("timings") or {})})},
        }
    return s


def compare(a: dict, b: dict) -> str:
    lines = [f"# Chat eval: {a['tag']} vs {b['tag']}", "",
             f"KB {a['tag']}: {a['kb']} (reranker {a['reranker']}, grounding {a['grounding']})",
             f"KB {b['tag']}: {b['kb']} (reranker {b['reranker']}, grounding {b['grounding']})", "",
             "## Retrieval (150 cases)", "", f"| slice | metric | {a['tag']} | {b['tag']} |", "|---|---|---|---|"]
    for sl in ("all", "en", "hi", "hinglish"):
        for m in ("hit@1", "hit@6", "mrr@6", "precision@6"):
            lines.append(f"| {sl} | {m} | {a['summary']['retrieval'][sl][m]} | {b['summary']['retrieval'][sl][m]} |")
    for q in ("p50", "p95"):
        lines.append(f"| all | search latency {q} (s) | {a['summary']['retrieval']['latency_s'][q]} | "
                     f"{b['summary']['retrieval']['latency_s'][q]} |")
    aa, bb = a["summary"].get("answers") or {}, b["summary"].get("answers") or {}
    if aa or bb:
        lines += ["", f"## Answers ({aa.get('n')} / {bb.get('n')} cases)", "",
                  f"| metric | {a['tag']} | {b['tag']} |", "|---|---|---|"]
        for k in ("urgency_ok", "over_triage", "required_recall", "banned_violations", "citation_precision",
                  "answers_with_citations", "grounding_rate", "flagged_not_in_sources", "errors"):
            lines.append(f"| {k} | {aa.get(k)} | {bb.get(k)} |")
        for q in ("p50", "p95"):
            lines.append(f"| latency {q} (s) | {(aa.get('latency_s') or {}).get(q)} | {(bb.get('latency_s') or {}).get(q)} |")
    ra, rb = {r["id"]: r for r in a["rows"]}, {r["id"]: r for r in b["rows"]}
    gained = [i for i in ra if not ra[i]["hit6"] and rb.get(i, {}).get("hit6")]
    lost = [i for i in ra if ra[i]["hit6"] and not rb.get(i, {}).get("hit6")]
    lines += ["", f"Retrieval hit@6 gained: {len(gained)} ({', '.join(gained[:30])})",
              f"Retrieval hit@6 lost: {len(lost)} ({', '.join(lost[:30])})"]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--tag", required=True)
    r.add_argument("--answers", type=int, default=0)
    r.add_argument("--only", help="regex on case id")
    c = sub.add_parser("compare")
    c.add_argument("a", type=Path)
    c.add_argument("b", type=Path)
    a = ap.parse_args()
    out = ROOT / "eval" / "reports"
    out.mkdir(exist_ok=True)
    if a.cmd == "run":
        rep = asyncio.run(run(a.tag, a.answers, a.only))
        p = out / f"chat_{a.tag}.json"
        p.write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(rep["summary"], indent=2))
        print(f"Report: {p}")
    else:
        md = compare(json.loads(a.a.read_text(encoding="utf-8")), json.loads(a.b.read_text(encoding="utf-8")))
        p = out / "chat_compare.md"
        p.write_text(md, encoding="utf-8")
        print(md)


if __name__ == "__main__":
    main()
