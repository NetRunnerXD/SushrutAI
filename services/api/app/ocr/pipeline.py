"""Prescription parsing pipeline.

Local vision models read the page. Gemini is used only when the person chose it and allowed full cloud answers.

1 load (PDF→image)  2 preprocess  3 OCR text (Surya if installed)  4 VLM structured read (Ollama)
5 fuse + normalise (abbreviations, drug-name resolution)  6 validate (allergy, interactions, banned FDC,
duplicates)  7 return a DRAFT that the user must confirm.
"""
from __future__ import annotations

import base64
import io
import logging
import re
from collections.abc import Awaitable, Callable

from PIL import Image, ImageOps
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.llm.providers import ProviderUnavailable, ollama
from app.medicine import service as med
from app.medicine.parse import _STRENGTH, detect_form, parse_composition
from app.ocr.abbreviations import parse_duration_days, parse_frequency, parse_timing

log = logging.getLogger("sushrut.ocr")
PIPELINE_VERSION = "rx-0.4.0"
_NOT_MEDICINE = re.compile(
    r"(?i)^\s*(diet|advice|adv|rest|review|follow[- ]?up|ice ?pack|warm (saline )?gargles?|plenty of (oral )?(fluids|liquids)|"
    r"exercise|physiotherapy|footwear|admit|admission|npo|investigations?|cbc|x-?ray|usg|ecg)\b")
LOW_CONFIDENCE = 0.85

from app.ocr.transcript import TRANSCRIBE_PROMPT, parse_transcript  # noqa: E402


def load_image(data: bytes, mime: str) -> Image.Image:
    if mime == "application/pdf":
        import fitz

        with fitz.open(stream=data, filetype="pdf") as doc:
            pix = doc[0].get_pixmap(dpi=200)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
    else:
        img = Image.open(io.BytesIO(data))
    return ImageOps.exif_transpose(img).convert("RGB")


def preprocess(img: Image.Image, max_side: int = 1280) -> Image.Image:
    """Resize for the VLM context budget and improve contrast. (OpenCV deskew/perspective when installed.)"""
    try:
        import cv2
        import numpy as np

        arr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2LAB)
        l, a, b = cv2.split(arr)
        l = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(l)
        img = Image.fromarray(cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2RGB))
    except ImportError:
        img = ImageOps.autocontrast(img, cutoff=1)
    scale = max_side / max(img.size)
    if scale < 1:
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    return img


def ocr_text(img: Image.Image) -> str | None:
    """Printed-text OCR via Surya, used as a hint for the VLM. Opt-in (RX_OCR_HINT=surya): it downloads ~1.5 GB of
    models on first use and has not yet been shown to improve the Qwen3-VL two-step reader on the Rx eval."""
    import os

    if os.getenv("RX_OCR_HINT", "").lower() != "surya":
        return None
    try:
        from surya.detection import DetectionPredictor
        from surya.recognition import RecognitionPredictor
    except ImportError:
        return None
    try:
        det, rec = DetectionPredictor(), RecognitionPredictor()
        preds = rec([img], det_predictor=det)
        return "\n".join(line.text for line in preds[0].text_lines)
    except Exception as e:  # surya API differs across versions — never fail the pipeline on the hint
        log.warning("Surya OCR failed: %s", e)
        return None


async def vlm_read(img: Image.Image, ocr: str | None, model_pref: str = "balanced") -> tuple[dict, str]:
    from app.llm.providers import doc_model_for

    s = get_settings()
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    b64 = base64.b64encode(buf.getvalue()).decode()
    hint = f"\nOCR text detected on the page (may contain errors):\n{ocr[:3000]}\n" if ocr else ""
    msgs = [{"role": "user", "content": TRANSCRIBE_PROMPT.format(ocr_hint=hint)}]
    from app.llm.providers import gemini, gemini_model_for
    if g_model := gemini_model_for(model_pref):
        try:
            res = await gemini().chat(g_model, msgs, images=[b64], temperature=0.0, num_predict=700)
            from app.ocr.indic import normalise_transcript
            text = await normalise_transcript(res.text)
            parsed = parse_transcript(text)
            parsed["_transcript"] = res.text
            if parsed.get("medicines"):
                return parsed, g_model
        except ProviderUnavailable as e:
            log.warning("Gemini prescription read failed (%s); using the local reader", e)
    available = await ollama().models()
    order = list(dict.fromkeys(m for m in (doc_model_for(model_pref), s.rx_vlm_model, s.vlm_model) if m))
    usable = [m for m in order if m in available or f"{m}:latest" in available]
    if not usable:
        raise ProviderUnavailable(f"No vision model available. Run: ollama pull {s.rx_vlm_model}")
    model = usable[0]
    # Step 1: plain-text transcription (no JSON constraint — that made small VLMs return empty lists).
    # 4k context keeps the VLM + vision encoder inside 6 GB VRAM.
    try:
        res = await ollama().chat(model, msgs, images=[b64], temperature=0.0, num_ctx=4096, num_predict=700)
        parsed = parse_transcript(res.text)
        failed = not parsed.get("medicines")
    except ProviderUnavailable:
        failed = True
    if failed and model != s.rx_vlm_model and s.rx_vlm_model in usable:
        model = s.rx_vlm_model  # the faster reader failed or read nothing: the default reader tries
        res = await ollama().chat(model, msgs, images=[b64], temperature=0.0, num_ctx=4096, num_predict=700)
    # Step 2: deterministic structuring of the transcript. Lines written in an Indian language are normalised
    # first (local dosing words → shorthand, Indian-script medicine names → English spelling); others unchanged.
    from app.ocr.indic import normalise_transcript
    text = await normalise_transcript(res.text)
    parsed = parse_transcript(text)
    parsed["_transcript"] = res.text
    return parsed, model


async def resolve_medicine(db: AsyncSession, name: str, strength: str, form: str) -> dict:
    query = f"{name} {strength}".strip()
    comp = parse_composition(f"{form} {query}")
    candidates = await med.search(db, query, limit=3)
    best = candidates[0] if candidates and candidates[0]["match_score"] >= 80 else None
    return {
        "candidates": [{"id": c["id"], "name": c["name"], "composition": c["composition"],
                        "score": c["match_score"], "is_janaushadhi": c["is_janaushadhi"]} for c in candidates],
        "product_id": best["id"] if best else None,
        "composition": best["composition"] if best else comp.as_list(),
        "match_score": best["match_score"] if best else None,
    }


async def parse_prescription(db: AsyncSession, data: bytes, mime: str, *,
                             progress: Callable[[str], Awaitable[None]],
                             allergies: list[str], current_generics: list[str], model_pref: str = "balanced") -> dict:
    await progress("loading")
    img = preprocess(load_image(data, mime))
    await progress("ocr")
    ocr = ocr_text(img)
    await progress("reading")
    raw, model = await vlm_read(img, ocr, model_pref)
    await progress("normalising")

    items = []
    seen: set[str] = set()
    for m in raw.get("medicines", []) or []:
        line = re.sub(r"\s+", " ", str(m.get("line") or "")).strip()
        name = re.sub(r"(?i)^\s*(tab|tabs|cap|caps|syp|syr|syrup|inj|susp|suspension|sachet|oint|t|c)\b\.?\s*", "",
                      (m.get("name") or "").strip())
        name = re.split(r"[:(]", name)[0].strip()  # "Allegra: Fexofenadine(30 mg) …" -> "Allegra"
        if not name or _NOT_MEDICINE.match(name) or not re.sub(r"\W", "", name):
            continue  # advice lines ("Diet", "Rest", "Ice pack") or a bare dosage-form prefix ("Cap.")
        key = name.lower()
        if key in seen:  # small models sometimes repeat a line
            continue
        seen.add(key)
        text = f"{line} {name}"
        strength = next((x.group(0) for x in _STRENGTH.finditer(name)), None) or             next((x.group(0) for x in _STRENGTH.finditer(line)), "")
        form = detect_form(text)
        resolved = await resolve_medicine(db, re.sub(re.escape(strength), "", name).strip() if strength else name,
                                          strength, form or "")
        # Model self-confidence is unreliable; a strong match to a known product is the only evidence we trust.
        conf = round(resolved["match_score"] / 100, 2) if resolved["match_score"] else 0.5
        items.append({
            "raw": line or name, "name": name, "strength": strength, "form": form,
            "frequency_raw": line, "frequency": parse_frequency(line),
            "timing": parse_timing(line), "duration_days": parse_duration_days(line), "instructions": "",
            "confidence": conf, "needs_review": conf < LOW_CONFIDENCE, **resolved,
        })

    await progress("checking")
    generics = [c["generic"] for it in items for c in (it["composition"] or []) if c.get("generic")]
    alerts = []
    lower_allergies = {a.lower() for a in allergies}
    for g in generics:
        if g in lower_allergies:
            alerts.append({"level": "high", "type": "allergy",
                           "text": f"This prescription contains {g}, which is listed in your allergies. "
                                   f"Contact your doctor before taking it."})
    dup = {g for g in generics if generics.count(g) > 1}
    for g in dup:
        alerts.append({"level": "moderate", "type": "duplicate",
                       "text": f"{g} appears in more than one medicine — check with your pharmacist."})
    for d in await med.interactions(db, generics + current_generics):
        alerts.append({"level": "high" if d["severity"] == "Major" else "moderate", "type": "interaction",
                       "text": f"Possible interaction: {d['a']} + {d['b']} ({d['severity']})."})
    for it in items:
        gs = [c["generic"] for c in it["composition"] or []]
        for b in await med.banned_check(db, gs):
            in_force = b["status"] == "in_force"
            alerts.append({"level": "high" if in_force else "moderate", "type": "banned_fdc",
                           "text": (f"'{it['name']}' matches an entry on CDSCO's prohibited-drugs list "
                                    f"({b['description']}). " + ("" if in_force else b["status_note"]))
                                   + " Show this prescription to your doctor or pharmacist."})

    return {
        "pipeline_version": PIPELINE_VERSION, "vlm_model": model, "ocr_engine": "surya" if ocr else None,
        "doctor": {"name": "", "qualification": "", "clinic": ""}, "date": "",
        "diagnosis": raw.get("diagnosis", []), "complaints": [], "vitals": {}, "medications": items,
        "tests": raw.get("tests", []), "advice": [], "follow_up": "", "alerts": alerts,
        "truncated": bool(raw.get("_truncated")),
        "raw_ocr": ocr, "transcript": raw.get("_transcript"),
        "notice": "Machine reading of prescriptions can be wrong. Check every medicine against the original "
                  "and confirm with your pharmacist before use.",
    }
