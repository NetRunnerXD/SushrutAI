"""Shared helpers for user-uploaded images and PDFs (chat attachments, lab reports, medical images)."""
from __future__ import annotations

import base64
import io
import re

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.llm.providers import ollama
from app.models import Document
from app.ocr.pipeline import load_image, preprocess
from app.routers.documents import read_document
from app.security.auth import CurrentUser

MODALITY_PROMPT = """What kind of image is this? Answer with exactly one word from this list:
chest_xray, other_xray, ct, mri, ultrasound, skin, eye_fundus, ecg, lab_report, prescription, document, photo, other"""
MODALITIES = {"chest_xray", "other_xray", "ct", "mri", "ultrasound", "skin", "eye_fundus", "ecg", "lab_report",
              "prescription", "document", "photo", "other"}


async def doc_image_b64(cu: CurrentUser, db: AsyncSession, doc_id: str, max_side: int = 1280) -> tuple[str, Document]:
    """A stored document as a JPEG for the vision model: PDFs are rendered (first page), large photos resized."""
    doc = await db.get(Document, doc_id)
    if doc is None or doc.user_id != cu.id:
        raise HTTPException(404, "Document not found")
    img = preprocess(load_image(read_document(cu, doc), doc.mime), max_side=max_side)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode(), doc


async def _ask(model: str, prompt: str, b64: str, n: int = 6, ctx: int = 2048) -> str:
    m = await ollama().chat(model, [{"role": "user", "content": prompt}], images=[b64], temperature=0.0,
                            num_ctx=ctx, num_predict=n)
    return m.text.strip().lower()


async def classify_modality(b64: str) -> str:
    """One-word image type from MedGemma. Good at telling scans apart (used by /images/analyze), unreliable for
    documents: on real samples it called prescriptions chest X-rays. Do not use it to route documents."""
    t = await _ask(get_settings().vlm_model, MODALITY_PROMPT, b64, n=8)
    word = re.sub(r"[^a-z_]", "", t.split()[0] if t else "other")
    return word if word in MODALITIES else "other"


# ---------------------------------------------------------------- attachment type (chat)
Q_SCAN = ("Look at this image. Is it a medical scan or X-ray film (chest X-ray, CT, MRI, ultrasound, ECG tracing) "
          "rather than a photo of a paper document or of a person? Answer only yes or no.")
Q_LAB = ("Does this image show laboratory test RESULTS: a list or table of test names with measured numbers, units "
         "(like mg/dL, g/dL, %, /cumm) or reference ranges? Answer only yes or no.")
Q_RX = ("Does this image show a doctor's PRESCRIPTION: medicines to take (tablet, capsule, syrup or injection names) "
        "with dose, timing or duration, often handwritten? Answer only yes or no.")
Q_DIRECT = ("Is this document a doctor's PRESCRIPTION (medicine names with doses or how often to take them, often "
            "handwritten) or a LAB REPORT (printed test names with result values and reference ranges)? "
            "Answer with one word: prescription or lab_report.")
Q_LINES = "Transcribe the first 15 lines of text in this image exactly as written. If there is no text, answer NO TEXT."

LAB_WORDS = re.compile(
    r"(?i)mg/dl|g/dl|gm/dl|/cumm|/ul\b|\bu/l\b|iu/l|mmol|meq|reference|ref\.? range|biological|normal range|"
    r"\bresults?\b|investigation|haemoglobin|hemoglobin|platelet|bilirubin|creatinine|cholesterol|pathology|"
    r"specimen|\bsample\b|laboratory|\blab\b")
RX_WORDS = re.compile(
    r"(?i)\brx\b|℞|\btab\.?\b|\bcap\.?\b|\bsyp\.?\b|\binj\.?\b|tablet|capsule|syrup|\b\d\s*-\s*\d\s*-\s*\d\b|"
    r"\b(od|bd|tds|qid|hs|sos)\b|x\s*\d+\s*(days?|d)\b|after food|before food|\badv\b|advice|c/o\b|follow[- ]?up")


def _yes(t: str) -> bool:
    return t.startswith("yes") or t.startswith("हाँ")


def _model() -> str:
    return get_settings().rx_vlm_model  # qwen3-vl: the stronger document reader on this machine


def _decide(scan: bool, lab: bool, rx: bool) -> str | None:
    if lab and not rx:
        return "lab_report"
    if rx and not lab:
        return "prescription"
    if scan and not lab and not rx:
        return "scan"
    if not (scan or lab or rx):
        return "photo"
    return None  # conflicting answers


async def _features(b64: str) -> dict:
    m = _model()
    scan, lab, rx = _yes(await _ask(m, Q_SCAN, b64)), _yes(await _ask(m, Q_LAB, b64)), _yes(await _ask(m, Q_RX, b64))
    return {"scan": scan, "lab": lab, "rx": rx}


async def _keywords(b64: str) -> tuple[int, int]:
    text = await _ask(_model(), Q_LINES, b64, n=220, ctx=4096)
    return len(LAB_WORDS.findall(text)), len(RX_WORDS.findall(text))


def _from_keywords(lab_n: int, rx_n: int) -> str:
    return "lab_report" if lab_n >= rx_n + 2 else "prescription" if rx_n >= lab_n + 2 else "unsure_document"


def _needs_keywords(f: dict) -> bool:
    return _decide(f["scan"], f["lab"], f["rx"]) is None


def decide_attachment(f: dict, keywords: tuple[int, int] | None = None) -> str:
    """Pure decision from the yes/no answers (+ page word counts when they conflict). Scored offline by
    eval/doc_type_eval.py --rescore."""
    kind = _decide(f["scan"], f["lab"], f["rx"])
    if kind is not None:
        return kind
    return _from_keywords(*keywords) if keywords else "unsure_document"


def decide_hybrid(f: dict, direct: str) -> str:
    """Yes/no answers say *whether* it is a document; the direct question says *which* document.
    Measured on real images (eval/doc_type_eval.py): the yes/no answers often miss documents entirely
    (all "no") while the direct question still names them correctly, so an all-"no" with a named document
    offers both readers instead of none."""
    scan, lab, rx = f["scan"], f["lab"], f["rx"]
    if scan and not lab and not rx:
        return "scan"
    if lab or rx:
        if direct in ("lab_report", "prescription"):
            return direct
        return _decide(scan, lab, rx) or "unsure_document"
    return "unsure_document" if direct in ("lab_report", "prescription") else "photo"


async def classify_attachment(b64: str) -> dict:
    """-> {"kind": prescription | lab_report | scan | photo | unsure_document, "evidence": {...}}.
    Separate yes/no questions per feature (naming a category made the small models pick the first option or
    always say "prescription"); when they conflict, words read from the page decide; if still unclear, say so
    — the app then offers both readers instead of guessing.
    The yes/no answers say whether it is a document; a direct "prescription or lab report?" question says which
    (decide_hybrid; on 50 real documents: 0 confidently wrong vs 7 for the yes/no answers alone)."""
    f = await _features(b64)
    if f["scan"] and not f["lab"] and not f["rx"]:
        return {"kind": "scan", "evidence": f}
    f["direct"] = (await _v_direct(b64))["kind"]
    kind = decide_hybrid(f, f["direct"])
    if kind == "unsure_document" and f["lab"] and f["rx"]:  # both said yes and the direct question was unclear
        kw = await _keywords(b64)
        f["keywords"] = {"lab": kw[0], "rx": kw[1]}
        kind = _from_keywords(*kw)
    return {"kind": kind, "evidence": f}


# ---------------------------------------------------------------- strategies measured in eval/doc_type_eval.py
async def _v_one_word(b64: str) -> dict:
    w = await classify_modality(b64)
    kind = {"lab_report": "lab_report", "prescription": "prescription", "document": "unsure_document"}.get(
        w, "scan" if w in ("chest_xray", "other_xray", "ct", "mri", "ultrasound", "ecg", "eye_fundus") else "photo")
    return {"kind": kind}


async def _v_direct(b64: str) -> dict:
    t = await _ask(_model(), Q_DIRECT, b64, n=8)
    return {"kind": "lab_report" if "lab" in t else "prescription" if "prescription" in t else "unsure_document"}


async def _v_features_only(b64: str) -> dict:
    f = await _features(b64)
    return {"kind": _decide(f["scan"], f["lab"], f["rx"]) or "unsure_document"}


async def _v_keywords_only(b64: str) -> dict:
    lab_n, rx_n = await _keywords(b64)
    return {"kind": _from_keywords(lab_n, rx_n)}


_VARIANTS = {"medgemma one-word": _v_one_word, "direct question": _v_direct, "yes/no features": _v_features_only,
             "page keywords": _v_keywords_only}
