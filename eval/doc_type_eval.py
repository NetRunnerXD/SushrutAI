"""Attachment type detection: prescription vs lab report vs medical scan vs other photo.

Real images, preprocessed exactly as the app does:
  - prescriptions: MIRAGE (data/eval/rx_mirage100) + the synthetic handwritten ones inside MedOCR-Vision
  - lab reports:   Indian lab reports inside MedOCR-Vision (data/eval/labs_medocr)
  - other:         shop receipts inside MedOCR-Vision (correct answer: "photo" = no document reader offered)
  - scans:         Kermany chest X-rays (data/eval/cxr_kermany)
MedOCR-Vision mixes all three document kinds, so each row is labelled from its ground-truth text.

    cd services/api && .venv/Scripts/python ../../eval/doc_type_eval.py --n 30 --other 10 --cxr 10

Every image's raw model answers (3 yes/no questions, the direct question, words read from the page) are recorded
once in eval/reports/doc_type_signals.json; the strategies are then scored from those signals, so a new decision
rule can be compared without re-running the models (--rescore).
Reported: accuracy, "unsure" (the app offers both readers), and confidently WRONG (the outcome that matters:
a prescription silently read as a lab report, or a document sent to the X-ray describer).
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))
DATA = ROOT / "data" / "eval"
SIGNALS = ROOT / "eval" / "reports" / "doc_type_signals.json"
REPORT_NAME = "doc_type_eval.json"
_LAB_TRUTH = re.compile(r"(?i)reference|ref\.? ?range|biological|normal (range|value)|haemoglobin|hemoglobin|"
                        r"cholesterol|glucose|creatinine|platelet|investigation|patholog|laborator|haematology|"
                        r"hematology|serum|\blab\b")


def truth_label(text: str) -> str:
    if "doctor_name" in text:
        return "prescription"
    if len(_LAB_TRUTH.findall(text)) >= 2:
        return "lab_report"
    return "photo"


def load_cases(n: int, n_other: int, n_cxr: int) -> list[tuple[str, str, str]]:
    import pyarrow.parquet as pq
    from app.ocr.pipeline import load_image, preprocess

    def b64(data: bytes, mime: str = "image/jpeg") -> str:
        img = preprocess(load_image(data, mime), max_side=1024)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=90)
        return base64.b64encode(buf.getvalue()).decode()

    def spread(items: list, k: int) -> list:
        return items[::max(1, len(items) // k)][:k] if k else []

    medocr = pq.read_table(DATA / "labs_medocr" / "test.parquet").to_pylist()
    by = {"prescription": [], "lab_report": [], "photo": []}
    for i, r in enumerate(medocr):
        by[truth_label(r["text"] or "")].append((f"medocr-{i}", r["image"]["bytes"]))
    rx = sorted((DATA / "rx_mirage100" / "images").glob("*.jpg"))
    n_mirage = n - n // 3
    cases = [("prescription", f"mirage-{p.stem}", b64(p.read_bytes())) for p in spread(rx, n_mirage)]
    cases += [("prescription", k, b64(d, "image/png")) for k, d in spread(by["prescription"], n - n_mirage)]
    cases += [("lab_report", k, b64(d, "image/png")) for k, d in spread(by["lab_report"], n)]
    cases += [("photo", k, b64(d, "image/png")) for k, d in spread(by["photo"], n_other)]
    if n_cxr:
        cxr = pq.read_table(DATA / "cxr_kermany" / "test.parquet").to_pylist()
        cases += [("scan", f"cxr-{i}", b64(r["image"]["bytes"])) for i, r in enumerate(spread(cxr, n_cxr))]
    return cases


async def collect(cases: list[tuple[str, str, str]]) -> list[dict]:
    from app.ocr import images as I

    old = {s["id"]: s for s in json.loads(SIGNALS.read_text("utf-8"))} if SIGNALS.exists() else {}
    out = []
    for i, (truth, cid, img) in enumerate(cases):
        if cid in old and old[cid].get("truth") == truth:
            out.append(old[cid])
            continue
        from app.llm.providers import ProviderUnavailable

        for attempt in range(3):
            try:
                t0 = time.time()
                f = await I._features(img)
                t_feat = time.time() - t0
                lab_n, rx_n = await I._keywords(img)
                direct = (await I._v_direct(img))["kind"]
                one_word = (await I._v_one_word(img))["kind"]
                break
            except ProviderUnavailable as e:
                print(f"[{i + 1}] retry after: {e}", flush=True)
                await asyncio.sleep(20)
        else:
            print(f"[{i + 1}] skipped {cid}", flush=True)
            continue
        out.append({"id": cid, "truth": truth, **f, "kw_lab": lab_n, "kw_rx": rx_n, "direct": direct,
                    "one_word": one_word, "sec_features": round(t_feat, 1), "sec_total": round(time.time() - t0, 1)})
        print(f"[{i + 1}/{len(cases)}] {truth:12} {out[-1]}", flush=True)
        SIGNALS.write_text(json.dumps(out, indent=0), encoding="utf-8")
    return out


def score(signals: list[dict]) -> dict:
    from app.ocr import images as I

    strategies = {
        "medgemma one-word": lambda s: s["one_word"],
        "direct question": lambda s: s["direct"],
        "yes/no features": lambda s: I._decide(s["scan"], s["lab"], s["rx"]) or "unsure_document",
        "page keywords": lambda s: I._from_keywords(s["kw_lab"], s["kw_rx"]),
        "app": lambda s: I.decide_attachment(s, (s["kw_lab"], s["kw_rx"])),
        "hybrid (features + direct)": lambda s: I.decide_hybrid(s, s["direct"]),
    }
    report = {}
    print(f"{len(signals)} images: " + ", ".join(f"{k} {v}" for k, v in Counter(s['truth'] for s in signals).items()))
    for name, fn in strategies.items():
        got = [(s["truth"], fn(s)) for s in signals]
        right = sum(t == g for t, g in got)
        unsure = sum(g == "unsure_document" for _, g in got)
        wrong = [(t, g) for t, g in got if g != t and g != "unsure_document"]
        per = {k: f"{sum(1 for t, g in got if t == k and g == k)}/{sum(1 for t, _ in got if t == k)}"
               for k in ("prescription", "lab_report", "scan", "photo")}
        detail = Counter(f"{t}->{g}" for t, g in wrong).most_common()
        print(f"{name:18} correct {right}/{len(got)}  unsure {unsure}  WRONG {len(wrong)}  {per}")
        print(f"{'':18} wrong: {detail}")
        report[name] = {"correct": right, "n": len(got), "unsure": unsure, "wrong": len(wrong), "per_class": per,
                        "wrong_detail": detail}
    feat = [s["sec_features"] for s in signals]
    report["seconds_per_image_features"] = round(sum(feat) / len(feat), 1)
    print(f"yes/no questions: {report['seconds_per_image_features']} s/image (GPU, qwen3-vl 4B)")
    (ROOT / "eval" / "reports" / REPORT_NAME).write_text(json.dumps(report, indent=1), encoding="utf-8")
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30, help="prescriptions and lab reports each")
    ap.add_argument("--other", type=int, default=10, help="non-medical photos of paper (receipts)")
    ap.add_argument("--cxr", type=int, default=10, help="chest X-rays")
    ap.add_argument("--rescore", action="store_true", help="score the recorded signals only (no model calls)")
    ap.add_argument("--model", help="document reader to test (e.g. qwen3-vl:2b-instruct); its signals and report "
                                    "go to separate files so the default reader's are kept")
    a = ap.parse_args()
    if a.model:
        import os

        os.environ["RX_VLM_MODEL"] = a.model  # read by app.config when the app is first imported (in collect)
        tag = re.sub(r"[^a-z0-9]+", "_", a.model.lower()).strip("_")
        SIGNALS = ROOT / "eval" / "reports" / f"doc_type_signals_{tag}.json"
        REPORT_NAME = f"doc_type_eval_{tag}.json"
    if a.rescore:
        score(json.loads(SIGNALS.read_text("utf-8")))
    else:
        score(asyncio.run(collect(load_cases(a.n, a.other, a.cxr))))
