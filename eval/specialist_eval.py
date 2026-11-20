"""Gate for the dedicated scan models in services/api/app/vision/specialists/.

A specialist is registered only if, on an independent public test set (not its training data), it reaches
AUC >= 0.85 and, at the chosen threshold, sensitivity >= 0.80 with specificity >= 0.75.

    cd services/api && .venv/Scripts/python -I ../../eval/specialist_eval.py cxr

Tasks:
  cxr   TorchXRayVision on Kermany paediatric chest X-rays (data/eval/cxr_kermany, test split: 390 pneumonia,
        234 normal). "Abnormal" score = the highest of the lung findings below. Also reports how often each
        finding is flagged on NORMAL films (what a healthy person would be told), to set per-finding thresholds.
Results: eval/reports/specialists.json (one entry per task).
"""
from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))
REPORT = ROOT / "eval" / "reports" / "specialists.json"
GATE = {"auc": 0.85, "sensitivity": 0.80, "specificity": 0.75}


def auc(pos: list[float], neg: list[float]) -> float:
    """Probability a random positive scores above a random negative (ties count half)."""
    p, n = np.asarray(pos), np.asarray(neg)
    return float(((p[:, None] > n[None, :]).sum() + 0.5 * (p[:, None] == n[None, :]).sum()) / (len(p) * len(n)))


def best_threshold(pos: list[float], neg: list[float], min_spec: float) -> dict:
    """Highest-sensitivity threshold whose specificity is >= min_spec."""
    best = None
    for t in sorted(set(pos) | set(neg)):
        sens = float(np.mean(np.asarray(pos) >= t))
        spec = float(np.mean(np.asarray(neg) < t))
        if spec >= min_spec and (best is None or sens > best["sensitivity"]):
            best = {"threshold": round(t, 4), "sensitivity": round(sens, 3), "specificity": round(spec, 3)}
    return best or {"threshold": None, "sensitivity": 0.0, "specificity": 0.0}


def save(task: str, result: dict) -> None:
    data = json.loads(REPORT.read_text("utf-8")) if REPORT.exists() else {}
    data[task] = result
    REPORT.write_text(json.dumps(data, indent=1), encoding="utf-8")


def task_cxr() -> dict:
    import pyarrow.parquet as pq
    from PIL import Image

    from app.vision.specialists.chest_xray import CHEST_XRAY, FLAG_AT

    assert CHEST_XRAY.available(), "torchxrayvision not installed"
    model = CHEST_XRAY._state["model"]
    lung = ["Pneumonia", "Consolidation", "Lung Opacity", "Infiltration", "Atelectasis"]
    rows = pq.read_table(ROOT / "data" / "eval" / "cxr_kermany" / "test.parquet").to_pylist()
    pos, neg, normal_flags = [], [], {}
    t0 = time.time()
    for r in rows:
        s = CHEST_XRAY.scores(model, Image.open(io.BytesIO(r["image"]["bytes"])))
        (pos if r["label"] == 1 else neg).append(max(s[k] for k in lung))
        if r["label"] == 0:
            for k, v in s.items():
                normal_flags.setdefault(k, []).append(v)
    sec = (time.time() - t0) / len(rows)
    a = auc(pos, neg)
    at_default = {"threshold": FLAG_AT, "sensitivity": round(float(np.mean(np.asarray(pos) >= FLAG_AT)), 3),
                  "specificity": round(float(np.mean(np.asarray(neg) < FLAG_AT)), 3)}
    best = best_threshold(pos, neg, GATE["specificity"])
    flagged_on_normal = {k: round(float(np.mean(np.asarray(v) >= FLAG_AT)), 3) for k, v in normal_flags.items()}
    # per-finding threshold so that at most 10% of normal films are flagged for it
    per_finding = {k: round(float(np.quantile(v, 0.9)), 3) for k, v in normal_flags.items()}
    passed = a >= GATE["auc"] and best["sensitivity"] >= GATE["sensitivity"] and best["specificity"] >= GATE["specificity"]
    result = {"model": CHEST_XRAY.model_id, "data": "Kermany paediatric CXR test split (390 pneumonia / 234 normal)",
              "auc": round(a, 3), "at_default_threshold": at_default, "best_at_spec_0.75": best,
              "flagged_on_normal_at_default": dict(sorted(flagged_on_normal.items(), key=lambda kv: -kv[1])),
              "per_finding_p90_on_normal": per_finding, "seconds_per_image": round(sec, 3), "gate": GATE,
              "passed": passed}
    print(json.dumps({k: v for k, v in result.items() if k != "per_finding_p90_on_normal"}, indent=1))
    return result


def task_cxr_calibrate(q_lung: float = 0.95, q_other: float = 0.975) -> dict:
    """Per-finding thresholds set on half of the films (even rows), reported on the other half (odd rows).
    Lung findings: flagged on at most 5% of calibration normals each; others: 2.5%."""
    import pyarrow.parquet as pq
    from PIL import Image

    from app.vision.specialists.chest_xray import CHEST_XRAY, LUNG

    assert CHEST_XRAY.available()
    model = CHEST_XRAY._state["model"]
    rows = pq.read_table(ROOT / "data" / "eval" / "cxr_kermany" / "test.parquet").to_pylist()
    scored = [(r["label"], CHEST_XRAY.scores(model, Image.open(io.BytesIO(r["image"]["bytes"])))) for r in rows]
    calib, test = scored[0::2], scored[1::2]
    keys = list(scored[0][1])
    thresh = {k: round(float(np.quantile([s[k] for y, s in calib if y == 0], q_lung if k in LUNG else q_other)), 3)
              for k in keys}

    def flags(s):
        return [k for k in keys if s[k] >= thresh[k]]

    normals = [flags(s) for y, s in test if y == 0]
    pneu = [flags(s) for y, s in test if y == 1]
    any_normal = float(np.mean([bool(f) for f in normals]))
    lung_pneu = float(np.mean([any(k in LUNG for k in f) for f in pneu]))
    from collections import Counter
    result = {"model": CHEST_XRAY.model_id, "thresholds": thresh, "q_lung": q_lung, "q_other": q_other,
              "test_half": {"normal_films": len(normals), "pneumonia_films": len(pneu),
                            "normal_any_flag_rate": round(any_normal, 3),
                            "pneumonia_lung_flag_rate": round(lung_pneu, 3),
                            "flags_on_normal": dict(Counter(k for f in normals for k in f).most_common()),
                            "flags_on_pneumonia": dict(Counter(k for f in pneu for k in f).most_common(8))}}
    print(json.dumps(result, indent=1))
    return result


def dhash(img, size: int = 8) -> int:
    """64-bit difference hash: near-identical images (resized, re-encoded) differ in only a few bits."""
    from PIL import Image

    g = np.asarray(img.convert("L").resize((size + 1, size), Image.BILINEAR), dtype=np.int16)
    bits = (g[:, 1:] > g[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


BONE_CANDIDATES = ["Heem2/bone-fracture-detection-using-xray", "prithivMLmods/Bone-Fracture-Detection"]


def task_bone() -> dict:
    """Fracture models on a FracAtlas sample (data/eval/fracatlas_sample: 150 fractured / 150 not), after removing
    any test image that also appears in the models' likely training set (Hemg/bone-fracture-detection)."""
    import torch
    from huggingface_hub import hf_hub_download
    from PIL import Image
    from transformers import AutoImageProcessor, AutoModelForImageClassification

    from PIL import ImageFile

    ImageFile.LOAD_TRUNCATED_IMAGES = True  # a few FracAtlas JPEGs are missing their last bytes
    base = ROOT / "data" / "eval" / "fracatlas_sample"
    test = [(p, 1) for p in sorted((base / "Fractured").glob("*.jpg"))] + \
           [(p, 0) for p in sorted((base / "Non_fractured").glob("*.jpg"))]
    # 1. overlap with the training set (near-duplicate difference hashes)
    import pyarrow.parquet as pq
    train = pq.read_table(hf_hub_download("Hemg/bone-fracture-detection",
                                          "data/train-00000-of-00001-4c0dff0dab82a7da.parquet",
                                          repo_type="dataset")).column("image").to_pylist()
    train_h = np.array([dhash(Image.open(io.BytesIO(r["bytes"]))) for r in train], dtype=np.uint64)
    overlap = []
    for p, y in test:
        h = np.uint64(dhash(Image.open(p)))
        if (np.array([bin(int(x)).count("1") for x in (train_h ^ h)]) <= 5).any():
            overlap.append(p.name)
    test = [(p, y) for p, y in test if p.name not in overlap]
    print(f"training images {len(train)}; test images also in training: {len(overlap)}; kept {len(test)}", flush=True)
    # 2. each candidate
    models = {}
    for rid in BONE_CANDIDATES:
        proc = AutoImageProcessor.from_pretrained(rid)
        model = AutoModelForImageClassification.from_pretrained(rid).eval()
        frac = [i for i, l in model.config.id2label.items() if l.lower() == "fractured"][0]
        pos, neg = [], []
        t0 = time.time()
        for p, y in test:
            with torch.no_grad():
                logits = model(**proc(images=Image.open(p).convert("RGB"), return_tensors="pt")).logits[0]
            pr = float(torch.softmax(logits, -1)[int(frac)])
            (pos if y == 1 else neg).append(pr)
        a = auc(pos, neg)
        best = best_threshold(pos, neg, GATE["specificity"])
        at_half = {"sensitivity": round(float(np.mean(np.asarray(pos) >= 0.5)), 3),
                   "specificity": round(float(np.mean(np.asarray(neg) < 0.5)), 3)}
        models[rid] = {"auc": round(a, 3), "at_0.5": at_half, "best_at_spec_0.75": best,
                       "seconds_per_image": round((time.time() - t0) / len(test), 3),
                       "passed": a >= GATE["auc"] and best["sensitivity"] >= GATE["sensitivity"]
                       and best["specificity"] >= GATE["specificity"]}
        print(rid, json.dumps(models[rid]), flush=True)
    return {"data": "FracAtlas sample, 150 fractured / 150 not (CC BY 4.0)", "overlap_removed": overlap,
            "n_test": len(test), "models": models, "gate": GATE}


def task_cxr_scores() -> dict:
    """One pass over the Kermany films: every finding's score for both classifier inputs (lung crop, trimmed whole
    film) saved to eval/reports/cxr_scores_current.json, so the input and thresholds can be chosen offline."""
    import pyarrow.parquet as pq
    from PIL import Image

    from app.vision.specialists.chest_xray import CHEST_XRAY

    assert CHEST_XRAY.available()
    model = CHEST_XRAY._state["model"]
    rows = pq.read_table(ROOT / "data" / "eval" / "cxr_kermany" / "test.parquet").to_pylist()
    out, seg, t0 = [], 0, time.time()
    for n, r in enumerate(rows, 1):
        img = Image.open(io.BytesIO(r["image"]["bytes"]))
        seg += CHEST_XRAY.prepare(img)["masks"] is not None
        out.append([r["label"], CHEST_XRAY.scores(model, img, "lungs"), CHEST_XRAY.scores(model, img, "frame")])
        if n % 50 == 0:
            print(f"[{n}/{len(rows)}] {(time.time() - t0) / n:.1f} s/film, anatomy found {seg}/{n}", flush=True)
    (ROOT / "eval" / "reports" / "cxr_scores_current.json").write_text(json.dumps(out), encoding="utf-8")
    return {"films": len(rows), "anatomy_found": seg, "file": "eval/reports/cxr_scores_current.json"}


LOC_CLASSES = {1: "Atelectasis", 4: "Consolidation", 6: "Effusion", 13: "Pneumothorax", 3: "Cardiomegaly"}
LOC_METHODS = ["gradcam_full", "gradcam", "occlusion"]


def task_loc(per_class: int = 30) -> dict:
    """Does "where the model looked" point at the real lesion? ChestX-Det test split (NIH films with
    radiologist-drawn lesion masks; HF mirror natealberti/ChestX-Det). For each film/finding pair:
      hit    = the strongest point of the map lies on the lesion (mask dilated by 2% of the image side)
      inside = share of the map's total heat that lies on the lesion
      chance = probability that a random point inside the lungs (or the image, without anatomy) hits the lesion
    Methods: gradcam_full (earlier behaviour), gradcam and occlusion on the lung crop, kept inside the anatomy.
    Caveats: NIH films were in the classifier's training data (labels, not locations) and ChestX-Det images may
    overlap the anatomy model's training data, so absolute numbers may be optimistic; the comparison is fair."""
    import random

    import pyarrow.parquet as pq
    from huggingface_hub import hf_hub_download
    from PIL import Image, ImageFilter

    from app.vision.specialists.chest_xray import CHEST_XRAY

    assert CHEST_XRAY.available()
    rows = pq.read_table(hf_hub_download("natealberti/ChestX-Det", "data/test-00000-of-00001.parquet",
                                         repo_type="dataset")).to_pylist()
    pairs = []
    for i, r in enumerate(rows):
        lab = np.asarray(Image.open(io.BytesIO(r["label"]["bytes"])))
        for cid in LOC_CLASSES:
            if (lab == cid).sum() > 200:
                pairs.append((i, cid))
    # The app only draws a map for a finding the model flags, so only those cases count: a quick pre-score on the
    # plain centre-cropped film keeps pairs where the finding scores >= 0.45 (no segmentation needed for that).
    import torch

    model = CHEST_XRAY._state["model"]
    scored: dict[int, dict] = {}

    def quick(i: int) -> dict:
        if i not in scored:
            img = Image.open(io.BytesIO(rows[i]["image"]["bytes"])).convert("L")
            W, H = img.size
            side = min(W, H)
            g = img.crop(((W - side) // 2, (H - side) // 2, (W - side) // 2 + side, (H - side) // 2 + side)).resize((224, 224))
            x = ((np.asarray(g, np.float32) / 255) * 2048 - 1024)[None, None]
            with torch.no_grad():
                scored[i] = dict(zip(model.pathologies, model(torch.from_numpy(x))[0].numpy()))
        return scored[i]

    candidates = [(i, cid) for i, cid in pairs if quick(i)[LOC_CLASSES[cid]] >= 0.45]
    print(f"lesion pairs {len(pairs)}; flagged by the model (kept) {len(candidates)}", flush=True)
    random.seed(5)
    picked = []
    for cid in LOC_CLASSES:
        ps = [p for p in candidates if p[1] == cid]
        picked += random.sample(ps, min(per_class, len(ps)))
    res = {m: {"hit": [], "inside": [], "by_class": {}} for m in LOC_METHODS}
    chance, segmented, t0 = [], 0, time.time()
    for n, (i, cid) in enumerate(picked, 1):
        r = rows[i]
        img = Image.open(io.BytesIO(r["image"]["bytes"])).convert("RGB")
        lab = np.asarray(Image.open(io.BytesIO(r["label"]["bytes"])))
        gt = lab == cid
        tol = max(3, round(0.02 * max(gt.shape)))
        gt_d = np.asarray(Image.fromarray((gt * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(2 * tol + 1))) > 127
        key = LOC_CLASSES[cid]
        prep = CHEST_XRAY.prepare(img)
        if prep["masks"] is not None:
            segmented += 1
        for m in LOC_METHODS:
            ex = CHEST_XRAY.explain(img, key, m)
            if ex is None:
                res[m]["hit"].append(0); res[m]["inside"].append(0.0)
                continue
            heat, outline = ex
            y, x = np.unravel_index(int(heat.argmax()), heat.shape)
            hit = int(gt_d[y, x]); inside = float((heat * gt).sum() / max(heat.sum(), 1e-6))
            res[m]["hit"].append(hit); res[m]["inside"].append(inside)
            res[m]["by_class"].setdefault(key, []).append(hit)
            if m == "occlusion":
                region = outline if (outline is not None and key != "Cardiomegaly") else np.ones_like(gt)
                chance.append(float((gt_d & region).sum() / max(region.sum(), 1)))
        if n % 10 == 0:
            print(f"[{n}/{len(picked)}] {(time.time() - t0) / n:.1f} s/pair  " + "  ".join(
                f"{m} hit {np.mean(res[m]['hit']):.2f}" for m in LOC_METHODS), flush=True)
    out = {"data": "ChestX-Det test split (NIH), radiologist lesion masks", "pairs": len(picked),
           "segmented_share": round(segmented / len(picked), 3), "chance_hit_random_point_in_lungs": round(float(np.mean(chance)), 3),
           "methods": {m: {"pointing_hit": round(float(np.mean(v["hit"])), 3), "heat_inside_lesion": round(float(np.mean(v["inside"])), 3),
                           "by_class": {k: round(float(np.mean(h)), 3) for k, h in v["by_class"].items()}} for m, v in res.items()},
           "caveat": "NIH films were in the classifier's training data (labels only); comparison between methods is fair."}
    print(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    task = sys.argv[1] if len(sys.argv) > 1 else "cxr"
    save(task, {"cxr": task_cxr, "cxr_calibrate": task_cxr_calibrate, "cxr_scores": task_cxr_scores,
                "bone": task_bone, "loc": task_loc}[task]())
    print(f"Report: {REPORT}")
