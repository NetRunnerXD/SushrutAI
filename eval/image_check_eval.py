"""Calibrate and score the upload checks in app/ocr/check.py on real images.

Health images (should pass):  MIRAGE prescriptions (data/eval/rx_mirage100), Indian lab reports and synthetic
                              prescriptions from MedOCR-Vision (data/eval/labs_medocr), Kermany chest X-rays.
Not health (should be flagged): Imagenette photos (data/eval/nonhealth_imagenette; ImageNet, research use only)
                              and the shop receipts inside MedOCR-Vision.
Bad photos (should be flagged): the same health images, degraded the way phone photos go wrong: out of focus
                              (Gaussian blur), hand shake (motion blur), too dark, and tiny.

    cd services/api && .venv/Scripts/python -I ../../eval/image_check_eval.py

Prints the score distributions, the rates at the thresholds in check.py, and writes eval/reports/image_check.json.
Relevance is scored on the original images; quality on images shown at the size the app sees them.
"""
from __future__ import annotations

import io
import json
import random
import statistics
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))
sys.path.insert(0, str(ROOT / "eval"))
DATA = ROOT / "data" / "eval"


def load_sets(n_lab: int = 40, n_cxr: int = 30) -> dict[str, list[tuple[str, Image.Image]]]:
    import pyarrow.parquet as pq
    from doc_type_eval import truth_label

    sets: dict[str, list] = {"prescription": [], "lab_report": [], "xray": [], "photo_nonhealth": [], "receipt": []}
    for p in sorted((DATA / "rx_mirage100" / "images").glob("*.jpg")):
        sets["prescription"].append((p.stem, Image.open(p).convert("RGB")))
    rows = pq.read_table(DATA / "labs_medocr" / "test.parquet").to_pylist()
    for i, r in enumerate(rows):
        kind = truth_label(r["text"] or "")
        key = {"lab_report": "lab_report", "prescription": "prescription", "photo": "receipt"}[kind]
        if key == "lab_report" and len(sets[key]) >= n_lab:
            continue
        sets[key].append((f"medocr-{i}", Image.open(io.BytesIO(r["image"]["bytes"])).convert("RGB")))
    cxr = pq.read_table(DATA / "cxr_kermany" / "test.parquet").to_pylist()
    random.seed(3)
    for i, r in enumerate(random.sample(cxr, n_cxr)):
        sets["xray"].append((f"cxr-{i}", Image.open(io.BytesIO(r["image"]["bytes"])).convert("RGB")))
    for p in sorted((DATA / "nonhealth_imagenette").glob("*.JPEG")):
        sets["photo_nonhealth"].append((p.stem, Image.open(p).convert("RGB")))
    return sets


def motion_blur(img: Image.Image, length: int) -> Image.Image:
    a = np.asarray(img, dtype=np.float32)
    out = np.zeros_like(a)
    for k in range(length):  # horizontal shake
        out += np.roll(a, k - length // 2, axis=1)
    return Image.fromarray((out / length).clip(0, 255).astype(np.uint8))


def degrade(img: Image.Image) -> dict[str, Image.Image]:
    long = max(img.size)
    r = long / 1000  # blur scaled to the photo's size, as a phone photo would be
    return {
        "blur_light": img.filter(ImageFilter.GaussianBlur(2.5 * r)),
        "blur_heavy": img.filter(ImageFilter.GaussianBlur(5 * r)),
        "shake": motion_blur(img, max(9, round(14 * r))),
        "dark": ImageEnhance.Brightness(img).enhance(0.18),
        "tiny": img.resize((max(1, img.width // 8), max(1, img.height // 8))),
    }


def main() -> None:
    from app.ocr import check as C

    assert C.available(), "CLIP model files missing (see app/ocr/check.py)"
    sets = load_sets()
    health_sets = ("prescription", "lab_report", "xray")
    rows = []
    for name, items in sets.items():
        for cid, img in items:
            rel = C.relevance(img)
            q = C.quality(img)
            kg = C.kind_guess(rel)
            rows.append({"set": name, "id": cid, "variant": "original", "health": rel["health"], "top": rel["top"],
                         "scan": rel["scan"], "kind": kg["kind"], "scan_type": kg["scan_type"],
                         "lab_share": kg["lab_share"], **q, "issue": C.check_image(img)["issue"]})
            if name in health_sets:
                for v, bad in degrade(img).items():
                    qb = C.quality(bad)
                    rows.append({"set": name, "id": cid, "variant": v, "scan": rel["scan"], **qb,
                                 "issue": C.quality_issue(qb, scan=rel["scan"])})
        print(f"{name}: {len(items)} images", flush=True)

    def dist(xs):
        xs = sorted(xs)
        return {"min": round(xs[0], 2), "p5": round(xs[max(0, len(xs) // 20)], 2), "median": round(statistics.median(xs), 2),
                "max": round(xs[-1], 2)} if xs else None

    report: dict = {"thresholds": {k: getattr(C, k) for k in ("SHARP_MIN", "SHARP_MIN_SCAN", "DARK_MAX", "MIN_SIDE", "SCAN_SHARE",
                                                              "HEALTH_MIN")}}
    print("\n== relevance (share of probability on health descriptions)")
    for name in sets:
        hs = [r["health"] for r in rows if r["set"] == name and r["variant"] == "original"]
        flagged = sum(h < C.HEALTH_MIN for h in hs)
        report[f"relevance_{name}"] = {"n": len(hs), "dist": dist(hs), "flagged_not_health": flagged}
        print(f"{name:16} n={len(hs):3}  {dist(hs)}  flagged not-health: {flagged}/{len(hs)}")

    print("\n== kind guess (cross-classification) and scan type")
    from collections import Counter
    for name in sets:
        rs = [r for r in rows if r["set"] == name and r["variant"] == "original"]
        kinds, types = Counter(r["kind"] for r in rs), Counter(r["scan_type"] for r in rs if r["scan_type"])
        report[f"kind_{name}"] = {"kinds": dict(kinds), "scan_types": dict(types)}
        print(f"{name:16} kinds {dict(kinds)}  scan types {dict(types)}")

    print("\n== quality on originals (should pass) and degraded copies (should be flagged)")
    for name in health_sets:
        for v in ("original", "blur_light", "blur_heavy", "shake", "dark", "tiny"):
            rs = [r for r in rows if r["set"] == name and r["variant"] == v]
            issues = [r["issue"] for r in rs if r["issue"] and r["issue"] != "not_health"]
            sh = dist([r["sharpness"] for r in rs])
            report[f"quality_{name}_{v}"] = {"n": len(rs), "flagged": len(issues), "sharpness": sh,
                                             "issues": {i: issues.count(i) for i in set(issues)}}
            print(f"{name:12} {v:11} flagged {len(issues):3}/{len(rs):3}  sharpness {sh}  "
                  f"{ {i: issues.count(i) for i in set(issues)} }")
    out = ROOT / "eval" / "reports" / "image_check.json"
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    (ROOT / "eval" / "reports" / "image_check_rows.json").write_text(json.dumps(rows), encoding="utf-8")
    print(f"\nReport: {out}")


if __name__ == "__main__":
    main()
