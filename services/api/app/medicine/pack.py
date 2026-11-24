"""Medicine strip / bottle / box photos: read the name and the printed composition, find the salt, compare it with
what the doctor prescribed. Used when someone shows a medicine to the camera and asks "What is this?" or "Is this
the same as what the doctor gave?" — the person does not need to be able to read the name."""
from __future__ import annotations

import logging
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.medicine.resolve import Resolved, resolve
from app.models import Medication
from app.security.auth import CurrentUser

log = logging.getLogger("sushrut.medicine")

PACK_SCHEMA = {
    "type": "object",
    "properties": {
        "is_medicine": {"type": "boolean"},
        "brand": {"type": "string"},
        "composition": {"type": "string"},   # as printed: "Paracetamol IP 650 mg"
        "form": {"type": "string"},
    },
    "required": ["is_medicine", "brand", "composition"],
}
_PROMPT = ("Is this a photo of a medicine (strip, blister, bottle, box or tube)? If yes, read exactly what is printed: "
           "the brand / product name, and the composition line (each salt with its strength, e.g. 'Paracetamol IP "
           "650 mg'). Leave a field empty if you cannot read it. Do not guess. Respond only with JSON.")

# "what is this medicine?", "is this the same as what the doctor gave?", "yeh kaunsi dawai hai"
MED_PHOTO_Q = re.compile(r"(?i)\b(what (is|are) (this|these)( medicine| tablet| strip)?|which medicine|"
                         r"same (as|medicine)|is this (the same|correct|right)|what the doctor gave|"
                         r"kaun ?si dawai|kya (yeh|ye) wahi|yeh kya hai)\b|कौन सी दवा|क्या यह वही|यह क्या है")
SAME_Q = re.compile(r"(?i)same|doctor gave|prescribed|wahi|वही|डॉक्टर")


def _pack_from(d: object) -> dict | None:
    if not isinstance(d, dict) or not d.get("is_medicine") or not (d.get("brand") or d.get("composition")):
        return None
    return {"brand": (d.get("brand") or "").strip()[:80], "composition": (d.get("composition") or "").strip()[:200],
            "form": (d.get("form") or "").strip()[:30]}


async def read_pack(b64: str, model_pref: str = "balanced") -> dict | None:
    """{"brand", "composition", "form"} when the photo is a medicine pack, else None."""
    from app.config import get_settings
    from app.llm.providers import ProviderUnavailable, gemini, gemini_model_for, ollama

    s = get_settings()
    if g_model := gemini_model_for(model_pref):
        try:
            res = await gemini().chat(g_model, [{"role": "user", "content": _PROMPT}], images=[b64],
                                     schema=PACK_SCHEMA, temperature=0.0, num_predict=200)
            if (pack := _pack_from(res.json())):
                return pack
        except (ProviderUnavailable, ValueError) as e:
            log.warning("Gemini pack read failed (%s); using the local reader", e)
    try:
        res = await ollama().chat(s.rx_vlm_model or s.vlm_model, [{"role": "user", "content": _PROMPT}],
                                  images=[b64], schema=PACK_SCHEMA, temperature=0.0, num_ctx=4096, num_predict=200)
        d = res.json()
    except (ProviderUnavailable, ValueError):
        return None
    return _pack_from(d)


async def compare_with_prescribed(db: AsyncSession, cu: CurrentUser, shown: Resolved) -> dict | None:
    """Is the medicine shown the same salt as one of the person's active medicines?"""
    if not shown.composition:
        return None
    meds = [cu.cipher.dec(m.name_enc) for m in await db.scalars(
        select(Medication).where(Medication.user_id == cu.id, Medication.status == "active"))]
    shown_g = {c.get("generic") for c in shown.composition}
    for name in meds:
        r = await resolve(db, name, use_model=False)
        if not r.composition:
            continue
        g = {c.get("generic") for c in r.composition}
        if g == shown_g:
            same_strength = r.composition_key == shown.composition_key
            return {"match": True, "medicine": name, "same_strength": same_strength,
                    "say": (f"Yes — this has the same salt as {name}, which is on your list."
                            + ("" if same_strength else " The strength is different, so check the dose with your pharmacist."))}
    if meds:
        return {"match": False, "say": f"This does not match the salt of the medicines on your list ({', '.join(meds[:3])}). "
                                       "Check with your pharmacist before taking it."}
    return None


# ---------------------------------------------------------------- names said or typed in a question
_AFTER_FORM = re.compile(r"(?i)\b(?:tab(?:let)?s?|cap(?:sule)?s?|syp|syrup|inj(?:ection)?|drops?|gel|cream)\.?\s+"
                         r"([A-Za-z][\w\-]{2,}(?:\s+\d{2,4}(?:\.\d+)?\s*(?:mg|ml)?)?)")
_BRAND_NUM = re.compile(r"\b([A-Z][a-zA-Z\-]{2,}(?:\s?-?\s?[A-Z]{1,3})?\s?\d{2,4})\b")  # "Dolo 650", "Pan-D 40", "Augmentin 625"


def names_in(text: str) -> list[str]:
    out: list[str] = []
    for m in [*_AFTER_FORM.finditer(text), *_BRAND_NUM.finditer(text)]:
        n = m.group(1).strip()
        if n.lower() not in (x.lower() for x in out):
            out.append(n)
    return out[:3]
