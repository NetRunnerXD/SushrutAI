"""Evaluate lab-report parsing and medical-image description on real files (evaluation only).

    cd services/api && .venv/Scripts/python ../../eval/reports_eval.py labs   [--limit N]
    cd services/api && .venv/Scripts/python ../../eval/reports_eval.py cxr    [--per-class N]

labs: MedOCR-Vision test split (printed Indian lab reports, ground-truth transcription with result tables).
      For every ground-truth test row with a numeric result we check that the pipeline extracted the same
      value, its reference range and the high/low flag, and whether it mapped the test to LOINC.
cxr:  hf-vision/chest-xray-pneumonia test split (Kermany et al. 2018, CC BY 4.0; label 0=NORMAL, 1=PNEUMONIA).
      Checks modality detection, whether the description mentions an abnormality on pneumonia films and avoids
      inventing one on normal films, and that it never states a definitive diagnosis.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import random
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

from rapidfuzz import fuzz  # noqa: E402

NUM = re.compile(r"\d+(?:\.\d+)?")


# ---------------------------------------------------------------- lab reports
def gt_rows(text: str) -> list[dict]:
    """Ground-truth result rows from the markdown tables in the transcription."""
    out = []
    # Tables may be on one line ("| a | b | | c | d |"): a "| |" boundary starts a new row.
    text = re.sub(r"\|\s+\|(?=\s*[A-Za-z])", "|\n|", text)
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3 or not re.match(r"[A-Za-z]", cells[0]) or set(cells[0]) <= set("-: ") or len(cells[0]) > 80:
            continue
        if cells[0].lower() in ("test", "investigation", "test name", "parameter", "test description"):
            continue
        m = NUM.search(cells[1])
        if not m:
            continue
        rng = NUM.findall(cells[3]) if len(cells) > 3 else []
        flag = "H" if re.search(r"\[H\]|\bH\b|high", cells[1], re.I) else "L" if re.search(r"\[L\]|\bL\b|low", cells[1], re.I) else None
        out.append({"name": cells[0], "value": float(m.group(0)), "unit": cells[2] if len(cells) > 2 else "",
                    "lo": float(rng[0]) if len(rng) >= 2 else None, "hi": float(rng[1]) if len(rng) >= 2 else None,
                    "flag": flag})
    return out


async def eval_labs(limit: int | None) -> dict:
    import pyarrow.parquet as pq

    from app.clinical.labs import parse_lab_report
    from app.config import get_settings
    from app.llm.providers import ollama
    from app.ocr.pipeline import load_image, preprocess
    from app.routers.reports import LAB_PROMPT

    rows = [r for r in pq.read_table(ROOT / "data/eval/labs_medocr/test.parquet").to_pylist()
            if re.search(r"(?i)reference|ref\. range|biological ref|normal range", r["text"]) and "|" in r["text"]]
    rows = rows[:limit] if limit else rows
    model = get_settings().rx_vlm_model
    tot = {"gt": 0, "found": 0, "value_ok": 0, "range_ok": 0, "range_n": 0, "flag_ok": 0, "flag_n": 0,
           "loinc": 0, "pred": 0, "pred_matched": 0}
    pages, lat = [], []
    for i, r in enumerate(rows, 1):
        gt = gt_rows(r["text"])
        if not gt:
            continue
        img = preprocess(load_image(r["image"]["bytes"], "image/png"), max_side=1600)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        t0 = time.time()
        res = await ollama().chat(model, [{"role": "user", "content": LAB_PROMPT}],
                                  images=[base64.b64encode(buf.getvalue()).decode()], temperature=0.0,
                                  num_ctx=6144, num_predict=1500)
        lat.append(time.time() - t0)
        pred = parse_lab_report(res.text)
        tot["pred"] += len(pred)
        used, page_found = set(), 0
        for g in gt:
            tot["gt"] += 1
            best, bs = None, 0.0
            for j, p in enumerate(pred):
                if j in used:
                    continue
                s = fuzz.token_set_ratio(g["name"].lower(), p.test_name.lower())
                if s > bs:
                    best, bs = j, s
            if best is None or bs < 70:
                continue
            used.add(best)
            p = pred[best]
            tot["found"] += 1
            page_found += 1
            tot["pred_matched"] += 1
            tot["value_ok"] += int(p.value is not None and abs(p.value - g["value"]) < 1e-6)
            tot["loinc"] += int(bool(p.loinc_code))
            if g["lo"] is not None:
                tot["range_n"] += 1
                tot["range_ok"] += int(p.ref_low == g["lo"] and p.ref_high == g["hi"])
                expected = "L" if g["value"] < g["lo"] else "H" if g["value"] > g["hi"] else "N"
                tot["flag_n"] += 1
                tot["flag_ok"] += int(p.flag == expected)
        pages.append({"page": i, "gt": len(gt), "found": page_found, "pred": len(pred), "seconds": round(lat[-1], 1)})
        print(f"[{i}/{len(rows)}] tests {page_found}/{len(gt)} found, {len(pred)} parsed, {lat[-1]:.1f}s", flush=True)

    def pct(a, b):
        return round(100 * a / b, 1) if b else None

    lat.sort()
    return {"task": "labs", "model": model, "pages": len(pages),
            "test_recall_pct": pct(tot["found"], tot["gt"]),
            "value_exact_pct_of_found": pct(tot["value_ok"], tot["found"]),
            "range_exact_pct": pct(tot["range_ok"], tot["range_n"]),
            "flag_correct_pct": pct(tot["flag_ok"], tot["flag_n"]),
            "loinc_mapped_pct_of_found": pct(tot["loinc"], tot["found"]),
            "precision_pct": pct(tot["pred_matched"], tot["pred"]),
            "median_seconds": round(lat[len(lat) // 2], 1) if lat else None, "counts": tot, "per_page": pages}


# ---------------------------------------------------------------- chest X-ray
ABNORMAL = re.compile(r"(?i)opacit|consolidat|infiltrat|pneumonia|haz(e|y)|patch|shadow|abnormal|cloud|white(r)? area|"
                      r"increased density|air.?space|effusion|unusual|dens(e|ity)")
NORMAL = re.compile(r"(?i)\b(clear|normal|no (obvious|clear|visible|significant) (abnormal|sign|opacit|problem))")
_DISCLAIMER_SENT = re.compile(r"(?i)radiologist|doctor|specialist|review|determine|assess|diagnos")
_NEG_BEFORE = re.compile(r"(?i)\b(no|not|without|nothing|absence of|free of|rule out)\b[^.;]{0,50}$")


def mentions_abnormality(text: str) -> bool:
    """True if the description reports a finding (any region), ignoring negated mentions ("no unusual shadows",
    "nothing suggesting pneumonia") and the radiologist-review disclaimer sentences."""
    for sent in re.split(r"(?<=[.!?])\s+", text):
        if _DISCLAIMER_SENT.search(sent):
            continue
        for m in ABNORMAL.finditer(sent):
            if not _NEG_BEFORE.search(sent[:m.start()]):
                return True
    return False


def rescore(path: Path) -> dict:
    d = json.loads(path.read_text(encoding="utf-8"))
    ex = d["examples"]
    pn = [e for e in ex if e["label"] == "PNEUMONIA"]
    nm = [e for e in ex if e["label"] == "NORMAL"]
    d["abnormality_mentioned_on_pneumonia_pct"] = round(100 * sum(mentions_abnormality(e["description"]) for e in pn) / len(pn), 1)
    d["abnormality_mentioned_on_normal_pct"] = round(100 * sum(mentions_abnormality(e["description"]) for e in nm) / len(nm), 1)
    d["rescored"] = True
    path.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    return d


DEFINITIVE = re.compile(r"(?i)\byou (definitely|certainly) have\b|\bthis (is|confirms) (pneumonia|tuberculosis|cancer)\b|"
                        r"\bdiagnosis is\b")


async def eval_cxr(per_class: int, seed: int, use_screen: bool = False, heldout: bool = False) -> dict:
    import pyarrow.parquet as pq

    from app.config import get_settings
    from app.llm.providers import ollama
    from app.ocr.pipeline import load_image, preprocess
    from app.routers.reports import MODALITY_PROMPT

    rows = pq.read_table(ROOT / "data/eval/cxr_kermany/test.parquet").to_pylist()
    if heldout:  # the half the chest X-ray screening thresholds were NOT set on (specialist_eval.py cxr_calibrate)
        rows = rows[1::2]
    rnd = random.Random(seed)
    sample = rnd.sample([r for r in rows if r["label"] == 1], per_class) + \
        rnd.sample([r for r in rows if r["label"] == 0], per_class)
    model = get_settings().vlm_model
    res = {"modality_ok": 0, "abn_mentioned_pneu": 0, "abn_mentioned_normal": 0, "definitive": 0, "n": 0}
    examples = []
    for i, r in enumerate(sample, 1):
        img = preprocess(load_image(r["image"]["bytes"], "image/jpeg"), max_side=896)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        b64 = base64.b64encode(buf.getvalue()).decode()
        m = await ollama().chat(model, [{"role": "user", "content": MODALITY_PROMPT}], images=[b64],
                                temperature=0.0, num_ctx=2048, num_predict=8)
        modality = re.sub(r"[^a-z_]", "", (m.text.strip().lower().split() or ["other"])[0])
        prompt = ("You are helping a patient in India understand a medical image (chest x-ray). Describe what is "
                  "visible in plain language, mention any areas that look unusual, and say clearly that only a "
                  "radiologist or specialist can give a diagnosis. Do not state a definitive diagnosis, do not "
                  "recommend medicines. Keep it under 150 words. Answer in English.")
        sc = None
        if use_screen:  # exactly what /images/analyze does: screen the original image, tell MedGemma
            from app.vision.screen import prompt_block, screen
            sc = await screen(load_image(r["image"]["bytes"], "image/jpeg"), "chest_xray")
            prompt += prompt_block(sc)
        d = await ollama().chat(model, [{"role": "user", "content": prompt}], images=[b64], temperature=0.2,
                                num_ctx=4096, num_predict=400)
        text = d.text
        abn = mentions_abnormality(text)
        res["n"] += 1
        res["modality_ok"] += int(modality == "chest_xray")
        if r["label"] == 1:
            res["abn_mentioned_pneu"] += int(abn)
        else:
            res["abn_mentioned_normal"] += int(abn)
        res["definitive"] += int(bool(DEFINITIVE.search(text)))
        if True:  # keep every description for audit
            examples.append({"label": "PNEUMONIA" if r["label"] == 1 else "NORMAL", "modality": modality,
                             "screen_flagged": [f["key"] for f in sc["flagged"]] if sc else None,
                             "description": text[:500]})
        print(f"[{i}/{len(sample)}] label={'PNEU' if r['label'] else 'NORM'} modality={modality} "
              f"abnormal_mentioned={abn}", flush=True)
    n = per_class
    return {"task": "cxr", "model": model, "per_class": n, "screening": use_screen, "heldout": heldout, "seed": seed,
            "modality_chest_xray_pct": round(100 * res["modality_ok"] / res["n"], 1),
            "abnormality_mentioned_on_pneumonia_pct": round(100 * res["abn_mentioned_pneu"] / n, 1),
            "abnormality_mentioned_on_normal_pct": round(100 * res["abn_mentioned_normal"] / n, 1),
            "definitive_diagnosis_statements": res["definitive"], "examples": examples}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("task", choices=["labs", "cxr", "rescore-cxr"])
    ap.add_argument("--file", type=Path, help="report to re-score (rescore-cxr)")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--per-class", type=int, default=15)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--screen", action="store_true", help="give MedGemma the chest X-ray screening model's findings")
    ap.add_argument("--heldout", action="store_true", help="only films the screening thresholds were not set on")
    a = ap.parse_args()
    if a.task == "rescore-cxr":
        d = rescore(a.file)
        print(json.dumps({k: v for k, v in d.items() if k != "examples"}, indent=2))
        return
    rep = asyncio.run(eval_labs(a.limit) if a.task == "labs" else eval_cxr(a.per_class, a.seed, a.screen, a.heldout))
    out = ROOT / "eval" / "reports"
    out.mkdir(exist_ok=True)
    p = out / f"{a.task}_{time.strftime('%Y%m%d-%H%M%S')}.json"
    p.write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in rep.items() if k not in ("per_page", "examples", "counts")}, indent=2))
    print(f"Report: {p}")


if __name__ == "__main__":
    main()
