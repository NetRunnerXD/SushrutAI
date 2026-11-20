"""Prescription-reading evaluation harness.

Runs the real SushrutAI Rx pipeline (app.ocr.pipeline) on labelled prescription images and scores it.

    cd services/api
    .venv/Scripts/python ../../eval/rx_eval.py ../../data/eval/rx_mirage100 --labels labels_silver_v1.json
    .venv/Scripts/python ../../eval/rx_eval.py ../../data/eval/rx_mirage100 --model medgemma:4b --limit 5

Metrics (per medicine line, labels marked `uncertain` are excluded from recall):
  name_recall      — share of labelled medicines that the pipeline found (fuzzy name match >= threshold)
  name_precision   — share of predicted medicines that match any labelled medicine (incl. uncertain)
  frequency_acc    — among matched medicines with a parseable labelled frequency, doses/day agree
  duration_acc     — among matched medicines with a labelled duration, days agree
  latency          — seconds per page (model load excluded only for the first page)
Writes reports/<timestamp>_<model>.json and .md next to the dataset.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

from rapidfuzz import fuzz  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.ocr.abbreviations import parse_duration_days, parse_frequency  # noqa: E402
from app.ocr.pipeline import parse_prescription  # noqa: E402

_PREFIX = re.compile(r"(?i)^\s*(tab|tabs|tablet|cap|caps|capsule|syp|syr|syrup|inj|injection|susp|suspension|"
                     r"sachet|oint|ointment|drops?|gel|t|c)\b\.?\s*")
_FORMWORDS = re.compile(r"(?i)\b(tablets?|capsules?|syrup|suspension|drops?|eye|ointment|sachet|mouthwash|"
                        r"mouth wash|injection|iv|i\.v\.)\b")


def norm(name: str) -> str:
    n = _PREFIX.sub("", name or "")
    n = _FORMWORDS.sub(" ", n)
    n = re.sub(r"[^a-z0-9 ]+", " ", n.lower())
    return re.sub(r"\s+", " ", n).strip()


def similarity(a: str, b: str) -> float:
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    # brand names are short: compare the first token strongly, the full string loosely
    head = fuzz.ratio(a.split()[0], b.split()[0])
    return max(fuzz.token_set_ratio(a, b) if min(len(a), len(b)) >= 4 else 0, head)


def match(labels: list[dict], preds: list[dict], threshold: float) -> list[tuple[int, int, float]]:
    """Greedy one-to-one matching by similarity."""
    pairs = sorted(((similarity(lab["name"], f"{p.get('name', '')} {p.get('strength', '')}"), i, j)
                    for i, lab in enumerate(labels) for j, p in enumerate(preds)), reverse=True)
    used_l, used_p, out = set(), set(), []
    for s, i, j in pairs:
        if s < threshold:
            break
        if i in used_l or j in used_p:
            continue
        used_l.add(i)
        used_p.add(j)
        out.append((i, j, s))
    return out


async def run(dataset: Path, labels_file: str, limit: int | None, threshold: float, model: str | None) -> dict:
    if model:
        s = get_settings()
        s.rx_vlm_model = model
    labels = json.loads((dataset / labels_file).read_text(encoding="utf-8"))
    pages = labels["pages"][:limit] if limit else labels["pages"]
    await init_db()

    async def progress(_: str) -> None:
        return None

    results, tot = [], {"label_certain": 0, "found": 0, "pred": 0, "pred_ok": 0, "freq_n": 0, "freq_ok": 0,
                        "dur_n": 0, "dur_ok": 0, "latency": [], "errors": 0}
    for k, page in enumerate(pages, 1):
        img = dataset / "images" / page["file"]
        data = img.read_bytes()
        t0 = time.perf_counter()
        try:
            async with SessionLocal() as db:
                parsed = await parse_prescription(db, data, "image/jpeg", progress=progress, allergies=[],
                                                  current_generics=[])
            err = None
        except Exception as e:  # noqa: BLE001 — record and continue
            parsed, err = {"medications": [], "vlm_model": None}, f"{type(e).__name__}: {e}"
            tot["errors"] += 1
        dt = time.perf_counter() - t0
        if k > 1:
            tot["latency"].append(dt)
        preds = parsed.get("medications", [])
        labs = page["medicines"]
        pairs = match(labs, preds, threshold)
        matched_l = {i for i, _, _ in pairs}
        certain = [i for i, lab in enumerate(labs) if not lab.get("uncertain")]
        found = [i for i in certain if i in matched_l]
        tot["label_certain"] += len(certain)
        tot["found"] += len(found)
        tot["pred"] += len(preds)
        tot["pred_ok"] += len(pairs)
        rows = []
        for i, j, s in pairs:
            lab, p = labs[i], preds[j]
            lf = parse_frequency(lab.get("frequency", "") or "")
            pf = p.get("frequency") or parse_frequency(p.get("frequency_raw", "") or "")
            f_ok = None
            if lf and lf.get("per_day") is not None and not lab.get("uncertain"):
                f_ok = bool(pf) and pf.get("per_day") == lf.get("per_day")
                tot["freq_n"] += 1
                tot["freq_ok"] += int(f_ok)
            ld = parse_duration_days(lab.get("duration", "") or "")
            d_ok = None
            if ld and not lab.get("uncertain"):
                d_ok = p.get("duration_days") == ld
                tot["dur_n"] += 1
                tot["dur_ok"] += int(d_ok)
            rows.append({"label": lab["name"], "pred": f"{p.get('name')} {p.get('strength', '')}".strip(),
                         "sim": round(s, 1), "freq_ok": f_ok, "dur_ok": d_ok,
                         "resolved_to": (p.get("candidates") or [{}])[0].get("name") if p.get("product_id") else None})
        missed = [labs[i]["name"] for i in certain if i not in matched_l]
        extra = [p.get("name") for j, p in enumerate(preds) if j not in {jj for _, jj, _ in pairs}]
        results.append({"file": page["file"], "style": page.get("style"), "seconds": round(dt, 1), "error": err,
                        "matched": rows, "missed": missed, "extra_predictions": extra})
        print(f"[{k}/{len(pages)}] {page['file']}: found {len(found)}/{len(certain)} certain, "
              f"{len(preds)} predicted, {dt:.1f}s{'  ERROR ' + err if err else ''}", flush=True)

    def pct(a: int, b: int) -> float | None:
        return round(100 * a / b, 1) if b else None

    lat = sorted(tot["latency"])
    summary = {
        "pages": len(pages), "errors": tot["errors"], "threshold": threshold,
        "model": get_settings().rx_vlm_model,
        "name_recall_pct": pct(tot["found"], tot["label_certain"]),
        "name_precision_pct": pct(tot["pred_ok"], tot["pred"]),
        "frequency_acc_pct": pct(tot["freq_ok"], tot["freq_n"]),
        "duration_acc_pct": pct(tot["dur_ok"], tot["dur_n"]),
        "counts": {k: v for k, v in tot.items() if k != "latency"},
        "latency_s": {"median": round(lat[len(lat) // 2], 1) if lat else None,
                      "max": round(lat[-1], 1) if lat else None},
        "labels": labels_file, "label_type": labels["meta"].get("label_type", "")[:60],
    }
    return {"summary": summary, "pages": results}


def write_report(dataset: Path, report: dict) -> Path:
    out = dataset / "reports"
    out.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    model = re.sub(r"[^a-zA-Z0-9.]+", "-", report["summary"]["model"])
    base = out / f"{stamp}_{model}"
    Path(f"{base}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    s = report["summary"]
    lines = [f"# Rx eval — {s['model']} — {stamp}", "",
             f"Labels: `{s['labels']}` ({s['label_type']}…)", "",
             "| Metric | Value |", "|---|---|",
             f"| Pages | {s['pages']} (errors: {s['errors']}) |",
             f"| Medicine name recall (certain labels) | {s['name_recall_pct']}% ({s['counts']['found']}/{s['counts']['label_certain']}) |",
             f"| Medicine name precision | {s['name_precision_pct']}% ({s['counts']['pred_ok']}/{s['counts']['pred']}) |",
             f"| Frequency accuracy (matched) | {s['frequency_acc_pct']}% ({s['counts']['freq_ok']}/{s['counts']['freq_n']}) |",
             f"| Duration accuracy (matched) | {s['duration_acc_pct']}% ({s['counts']['dur_ok']}/{s['counts']['dur_n']}) |",
             f"| Latency per page (median / max) | {s['latency_s']['median']} s / {s['latency_s']['max']} s |",
             "", "## Per page", ""]
    for p in report["pages"]:
        lines.append(f"**{p['file']}** ({p['style']}, {p['seconds']} s){' — ERROR ' + p['error'] if p['error'] else ''}")
        for m in p["matched"]:
            lines.append(f"- ✓ {m['label']} ← {m['pred']} (sim {m['sim']}, freq {m['freq_ok']}, dur {m['dur_ok']})")
        for m in p["missed"]:
            lines.append(f"- ✗ missed: {m}")
        for m in p["extra_predictions"]:
            lines.append(f"- ? extra: {m}")
        lines.append("")
    Path(f"{base}.md").write_text("\n".join(lines), encoding="utf-8")
    return base


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dataset", type=Path)
    ap.add_argument("--labels", default="labels_silver_v1.json")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--threshold", type=float, default=80.0)
    ap.add_argument("--model", help="override RX_VLM_MODEL, e.g. qwen2.5vl:3b or medgemma:4b")
    a = ap.parse_args()
    report = asyncio.run(run(a.dataset, a.labels, a.limit, a.threshold, a.model))
    base = write_report(a.dataset, report)
    print(json.dumps(report["summary"], indent=2))
    print(f"Report: {base}.md")


if __name__ == "__main__":
    main()
