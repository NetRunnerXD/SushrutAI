"""Run the dedicated screening model for a scan (if one passed its gate) and turn its findings into
(a) a block for MedGemma's prompt and (b) a plain-language "Screening check" for the person.

The scan type comes from MedGemma's one-word modality answer, which the image path already asks for: on the
Kermany chest X-rays it named 100% of them correctly, while the CLIP upload check could not tell chest from bone
X-rays (4/30 typed "chest"), so CLIP is used for the section check only.
"""
from __future__ import annotations

import asyncio

from PIL import Image

from app.vision.specialists import for_scan_type

MODALITY_TO_TYPE = {"chest_xray": "chest_xray", "other_xray": "bone_xray", "mri": "mri", "ct": "ct",
                    "ultrasound": "ultrasound", "ecg": "ecg", "skin": "skin"}


MAX_OVERLAYS = 4  # a picture for every flagged finding (at most 5 exist; each ~40-80 KB)
# How far each picture can be trusted, shown next to it:
#   "tested"  - the map pointed at radiologist-drawn lesions clearly better than chance (eval/specialist_eval.py loc,
#               ChestX-Det, occlusion inside the lungs: consolidation 69%, effusion 57% vs a random point 28%)
#   "anatomy" - enlarged heart: the picture is the heart's own outline from the anatomy model, not attention
#               (attention maps for it hit only 13%)
#   "rough"   - not checked against doctors' markings (fluid in the lungs, collapsed lung): a rough guide
REGION_QUALITY = {"Lung": "tested", "Effusion": "tested", "Cardiomegaly": "anatomy", "Edema": "rough",
                  "Pneumothorax": "rough"}


def _run(s, img: Image.Image):
    from app.vision.overlay import render

    findings = s.run(img)
    if findings is None:
        return None
    flagged = sorted((f for f in findings if f.flagged), key=lambda f: -f.score)
    overlays = {}
    for f in flagged[:MAX_OVERLAYS]:
        try:
            ex = s.explain(img, f.key)
            if ex is not None:
                overlays[f.key] = render(img, *ex)
        except Exception:  # noqa: BLE001 - the picture is extra; the findings still count
            pass
    return findings, flagged, overlays


async def screen(img: Image.Image, modality: str) -> dict | None:
    """-> {"model", "model_id", "scan_type", "flagged": [{key, plain_en, plain_hi, band, overlay}], "checked": [...],
    "none_flagged"} or None when no specialist covers this scan type (or it is unavailable)."""
    scan_type = MODALITY_TO_TYPE.get(modality)
    s = for_scan_type(scan_type)
    if s is None:
        return None
    r = await asyncio.to_thread(_run, s, img)
    if r is None:
        return None
    findings, flagged, overlays = r
    return {"model": s.name, "model_id": s.model_id, "scan_type": scan_type,
            "flagged": [{"key": f.key, "plain_en": f.plain_en, "plain_hi": f.plain_hi, "band": f.band,
                         "overlay": overlays.get(f.key), "region": REGION_QUALITY.get(f.key, "rough") if f.key in overlays else None}
                        for f in flagged],
            "checked": [{"key": f.key, "plain_en": f.plain_en, "plain_hi": f.plain_hi} for f in findings],
            "none_flagged": not flagged}


def prompt_block(sc: dict | None) -> str:
    """Context for MedGemma: what the screening model saw. Worded so it can disagree, and never diagnoses."""
    if not sc:
        return ""
    if sc["none_flagged"]:
        # Do not list the findings it looked for: naming "hazy patches" even as absent made MedGemma describe
        # hazy patches on normal films (false alarms 30% -> 80% in eval/reports_eval.py cxr --screen, 2026-11-21).
        seen = "It found nothing unusual in the lungs, the heart or around the lungs."
    else:
        seen = "It flagged: " + "; ".join(f["plain_en"].split(" — ")[0] for f in sc["flagged"]) + "."
    return (f"\nA separate {sc['model']} looked at this image first. {seen} Screening models can be wrong. Say in plain "
            "words whether what you see is consistent with this, and do not state a diagnosis.")


def for_person(sc: dict | None, lang: str) -> dict | None:
    """The 'Screening check' card: flagged areas in the person's language (Hindi or English) with a validated band
    ("possible" / "likely") and, for the strongest ones, a picture of where the model looked. No raw scores."""
    if not sc:
        return None
    key = "plain_hi" if lang == "hi" else "plain_en"
    return {"model": sc["model"], "scan_type": sc["scan_type"],
            "flagged": [{"key": f["key"], "text": f[key], "band": f.get("band"), "overlay": f.get("overlay"),
                         "region": f.get("region")} for f in sc["flagged"]],
            "none_flagged": sc["none_flagged"]}
