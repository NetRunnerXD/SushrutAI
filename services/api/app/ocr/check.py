"""Quick checks on an uploaded image, before any big model sees it.

1. Quality (no model): too blurry, too dark, washed out, blank, or too small to read. Sharpness is the variance of
   the Laplacian measured on the sharpest tiles of the image, so plain areas (paper margins, the dark parts of an
   X-ray) don't make a sharp photo look blurry.
2. Relevance: OpenAI CLIP ViT-B/32 (MIT), int8 ONNX export from Xenova/clip-vit-base-patch32, run on the CPU so
   the 6 GB GPU stays free. Zero-shot: the image is compared with short descriptions of health images
   (prescriptions, lab reports, scans, medicines, skin, wounds) and of everyday photos (food, pets, places,
   receipts, screenshots); the share that goes to the health descriptions decides.

Both only *advise*: the app asks the person to retake or send anyway. Thresholds were set on real images by
eval/image_check_eval.py (report: eval/reports/image_check.json). If the model files are missing, relevance is
skipped (None) and uploads still work. Disable with IMAGE_RELEVANCE=off.
"""
from __future__ import annotations

import logging
import os
import threading
import time

import numpy as np
from PIL import Image

log = logging.getLogger(__name__)
REPO = "Xenova/clip-vit-base-patch32"

# ---------------------------------------------------------------- thresholds (set by eval/image_check_eval.py)
SHARP_MIN = 30.0        # sharpness (see sharpness()) below this = blurry (real documents >= 52; blurred <= 25)
SHARP_MIN_SCAN = 4.0    # X-rays are smooth by nature (real >= 6.9; blurred <= 2.1)
DARK_MAX = 60.0         # mean brightness (0-255) below this = too dark (real images: >= 95; dark copies: <= 45)
MIN_SIDE = 250          # pixels
HEALTH_MIN = 0.6        # share of CLIP probability on the health descriptions; below = probably not health
HEALTH_CLEAR_MIN = 0.3  # below this it is clearly not health-related, reported before blur/darkness
                        # (lowest real health image: 0.47; blurred, shaken or dark copies of 265 health images:
                        # lowest 0.41, none below 0.3; shrunk copies: 1 of 265 below 0.3)
SCAN_SHARE = 0.25       # share on the scan descriptions at or above this = treat as a scan (CLIP's single best match
                        # for chest X-rays is often "printed prescription", but their scan share is >= 0.26;
                        # documents: median 0.08, p95 0.22)

HEALTH = [
    "a photo of a doctor's handwritten prescription",
    "a photo of a printed medical prescription",
    "a photo of a medical laboratory test report with results",
    "a photo of a blood test report",
    "a hospital discharge summary document",
    "a chest x-ray",
    "an x-ray image of bones",
    "a CT scan image",
    "an MRI scan image",
    "an ultrasound scan image",
    "an ECG tracing on paper",
    "a photo of a strip of medicine tablets",
    "a photo of a medicine bottle or medicine box",
    "a close-up photo of a skin rash",
    "a close-up photo of a wound or injury on the body",
    "a close-up photo of an eye or a tongue or a throat for a doctor",
]
OTHER = [
    "a photo of food on a plate",
    "a photo of a dog or a cat",
    "a photo of an animal or a fish",
    "a photo of a landscape or scenery",
    "a photo of a building or a church",
    "a photo of a car, truck or vehicle",
    "a selfie or a portrait of a person smiling",
    "a group photo of people",
    "a screenshot of a phone chat or social media",
    "a funny meme with text",
    "a photo of a shopping receipt or a bill",
    "a photo of furniture or a room",
    "a photo of a musical instrument",
    "a photo of sports equipment or a ball",
    "a photo of machinery or tools",
]
SCAN_LABELS = {5, 6, 7, 8, 9, 10}  # indexes in HEALTH that are scans (smooth images)
RX_LABELS = [0, 1, 11, 12]          # prescriptions and medicines
LAB_LABELS = [2, 3, 4]              # lab reports and other printed medical documents
SKIN_LABELS = [13, 14]
SCAN_TYPES = {5: "chest_xray", 6: "bone_xray", 7: "ct", 8: "mri", 9: "ultrasound", 10: "ecg"}

# Which kind of upload is this? (cross-classification: a lab report or an X-ray uploaded as a prescription, ...)
# lab share = P(lab descriptions) / P(lab + prescription descriptions). On the local eval images:
# real lab reports 0.86-1.00 (47/47 >= 0.85); prescriptions median 0.22, ~1% >= 0.85, ~85% < 0.5.
LAB_SURE = 0.85         # in the prescription section, "looks like a lab report" only at or above this
RX_SURE = 0.5           # in the lab section, "looks like a prescription" only below this

_lock = threading.Lock()
_state: dict = {}


# ---------------------------------------------------------------- quality
def _gray(img: Image.Image, side: int = 768) -> np.ndarray:
    g = img.convert("L")
    s = side / max(g.size)
    if s < 1:
        g = g.resize((max(1, round(g.width * s)), max(1, round(g.height * s))), Image.BILINEAR)
    return np.asarray(g, dtype=np.float32)


def _tile_p90(d: np.ndarray, tile: int) -> float:
    h, w = d.shape
    vals = [d[y:y + tile, x:x + tile].var() for y in range(0, h - tile + 1, tile) for x in range(0, w - tile + 1, tile)]
    return float(np.percentile(vals, 90)) if vals else float(d.var())


def sharpness(a: np.ndarray, tile: int = 64) -> float:
    """Second-derivative energy on the sharpest tiles, in the weaker of the two directions (higher = sharper).
    Taking the weaker direction catches hand shake, which smears text along one direction but leaves lines
    running the other way (table rules) sharp."""
    dx = a[:, :-2] + a[:, 2:] - 2 * a[:, 1:-1]
    dy = a[:-2, :] + a[2:, :] - 2 * a[1:-1, :]
    return min(_tile_p90(dx, tile), _tile_p90(dy, tile))


def quality(img: Image.Image) -> dict:
    a = _gray(img)
    mean, std = float(a.mean()), float(a.std())
    return {"sharpness": round(sharpness(a), 1), "brightness": round(mean, 1), "contrast": round(std, 1),
            "width": img.width, "height": img.height}


def quality_issue(q: dict, scan: bool = False) -> str | None:
    if min(q["width"], q["height"]) < MIN_SIDE:
        return "too_small"
    if q["brightness"] < DARK_MAX:
        return "too_dark"
    if q["sharpness"] < (SHARP_MIN_SCAN if scan else SHARP_MIN):
        return "blurry"
    return None


# ---------------------------------------------------------------- relevance (CLIP)
def _load():
    if "vision" in _state or "error" in _state:
        return _state.get("vision")
    with _lock:
        if "vision" in _state or "error" in _state:
            return _state.get("vision")
        if os.environ.get("IMAGE_RELEVANCE", "on").lower() in ("off", "0", "false", "no"):
            _state["error"] = "disabled"
            return None
        try:
            import onnxruntime as ort
            from huggingface_hub import hf_hub_download
            from tokenizers import Tokenizer

            offline = os.environ.get("HF_HUB_OFFLINE") == "1"
            get = lambda f: hf_hub_download(REPO, f, local_files_only=offline)  # noqa: E731
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = min(4, max(1, (os.cpu_count() or 4) // 2))
            vision = ort.InferenceSession(get("onnx/vision_model_quantized.onnx"), opts, providers=["CPUExecutionProvider"])
            text = ort.InferenceSession(get("onnx/text_model_quantized.onnx"), opts, providers=["CPUExecutionProvider"])
            tok = Tokenizer.from_file(get("tokenizer.json"))
            embs = []
            for p in HEALTH + OTHER:  # one at a time: the export pools at the first end-of-text token, no padding
                ids = np.array([tok.encode(p).ids], dtype=np.int64)
                embs.append(text.run(None, {"input_ids": ids})[0][0])
            t = np.stack(embs)
            _state.update(vision=vision, labels=t / np.linalg.norm(t, axis=1, keepdims=True))
        except Exception as e:  # noqa: BLE001 - optional component
            log.warning("image relevance check unavailable: %s", e)
            _state["error"] = str(e)
            return None
    return _state["vision"]


def available() -> bool:
    return _load() is not None


_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)


def _pixels(img: Image.Image) -> np.ndarray:
    """CLIP preprocessing: shortest side 224 (bicubic), centre crop 224, scale, normalise, CHW."""
    img = img.convert("RGB")
    s = 224 / min(img.size)
    img = img.resize((max(224, round(img.width * s)), max(224, round(img.height * s))), Image.BICUBIC)
    l, t = (img.width - 224) // 2, (img.height - 224) // 2
    a = (np.asarray(img.crop((l, t, l + 224, t + 224)), dtype=np.float32) / 255.0 - _MEAN) / _STD
    return a.transpose(2, 0, 1)[None]


def relevance(img: Image.Image) -> dict | None:
    """{"health": share of probability on health descriptions, "top": best description, "scan": bool}."""
    vision = _load()
    if vision is None:
        return None
    e = vision.run(None, {"pixel_values": _pixels(img)})[0][0]
    e = e / np.linalg.norm(e)
    logits = 100.0 * (_state["labels"] @ e)
    p = np.exp(logits - logits.max())
    p /= p.sum()
    top = int(p.argmax())
    labels = HEALTH + OTHER
    health = float(p[:len(HEALTH)].sum())
    scan = float(p[sorted(SCAN_LABELS)].sum())
    rx, lab, skin = float(p[RX_LABELS].sum()), float(p[LAB_LABELS].sum()), float(p[SKIN_LABELS].sum())
    best_scan = max(SCAN_TYPES, key=lambda i: p[i])
    return {"health": round(health, 3), "top": labels[top], "scan": scan >= SCAN_SHARE,
            "scan_share": round(scan, 3), "lab_share": round(lab / max(rx + lab, 1e-6), 3),
            "skin_share": round(skin, 3),
            "scan_type": SCAN_TYPES[best_scan] if scan >= SCAN_SHARE else ("skin" if skin >= 0.5 else None)}


def kind_guess(rel: dict | None) -> dict | None:
    """What the upload looks like: {"kind": scan | lab_report | prescription | unsure, "scan_type", "lab_share"}.
    'unsure' = between the two document thresholds; callers decide per section using LAB_SURE / RX_SURE."""
    if rel is None:
        return None
    lab, scan_type = rel.get("lab_share", 0.5), rel.get("scan_type")
    if rel.get("scan") or scan_type == "skin":
        kind = "scan"
    elif lab >= LAB_SURE:
        kind = "lab_report"
    elif lab < RX_SURE:
        kind = "prescription"
    else:
        kind = "unsure"
    return {"kind": kind, "scan_type": scan_type, "lab_share": lab}


# ---------------------------------------------------------------- both
def check_image(img: Image.Image, pdf: bool = False) -> dict:
    """-> {"issue": None | too_small | too_dark | blurry | not_health, "quality": {...},
    "relevance": {...} | None, "ms": int}. PDFs are rendered pages, so only relevance is checked for them."""
    t0 = time.perf_counter()
    rel = relevance(img)
    q = quality(img)
    if rel is not None and rel["health"] < HEALTH_CLEAR_MIN:
        issue = "not_health"  # clearly unrelated (a pet, a selfie): "retake" would be the wrong advice
    else:
        issue = None if pdf else quality_issue(q, scan=bool(rel and rel["scan"]))
        if issue is None and rel is not None and rel["health"] < HEALTH_MIN:
            issue = "not_health"
    return {"issue": issue, "quality": q, "relevance": rel, "kind": kind_guess(rel),
            "ms": round((time.perf_counter() - t0) * 1000)}
