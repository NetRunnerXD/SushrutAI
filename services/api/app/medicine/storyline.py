"""Medicine storyline: one short, continuous explanation instead of separate facts.

"The doctor gave Dolo 650. Its main salt is Paracetamol 650 mg. Jan Aushadhi has Paracetamol Tablets IP 650 mg
with the same salt for about ₹2 a tablet. Last time you took paracetamol, you said your fever came down."

Built only from: the resolved salt (app/medicine/resolve.py), same-composition products in the drug master, the
person's own medicine-response memory, and the safety notes (narrow-therapeutic-index, banned combinations).
"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.medicine import service
from app.medicine.resolve import Resolved
from app.models import HealthFact, Product
from app.security.auth import CurrentUser

EFFECT_EN = {"better": "it helped", "same": "it did not change much", "worse": "you felt worse",
             "side_effect": "it gave you a side effect"}


async def storyline(db: AsyncSession, cu: CurrentUser | None, r: Resolved, *, given_by_doctor: bool = True) -> dict:
    """{"text": the narrative (English), "parts": what it was built from, for the card}."""
    if r.source == "none" or not r.composition:
        return {"text": f"I could not find the salt in {r.query}. The salt name is printed on the strip under the "
                        "brand name — read it to me, or show me a photo of the strip.",
                "resolved": r.as_dict(), "alternatives": [], "warnings": []}
    salt = r.salt_text
    lead = f"The doctor gave {r.query}." if given_by_doctor else f"This is {r.query}."
    parts = [lead]
    if r.verified:
        parts.append(f"Its main salt is {salt}." if " + " not in salt else f"It contains {salt}.")
    else:
        parts.append(f"Its salt is probably {salt} — please check the strip, where the salt is printed under the name.")
    alts: list[dict] = []
    warnings: list[str] = []
    p = await db.scalar(select(Product).where(Product.composition_key == r.composition_key).limit(1)) if r.composition_key else None
    if p is not None:
        a = await service.alternatives(db, p)
        pool = [a["product"], *a["alternatives"]]
        ja = sorted((x for x in pool if x.get("is_janaushadhi") and (x.get("price") or {}).get("per_unit_inr")),
                    key=lambda x: x["price"]["per_unit_inr"])
        if ja:
            best = ja[0]
            per = best["price"]["per_unit_inr"]
            unit = {"tablet": "tablet", "capsule": "capsule"}.get((best.get("form") or "").lower(), "unit")
            same = "the same salt" if r.verified else "this salt"
            parts.append(f"Jan Aushadhi has {best['name']} with {same} for about ₹{per:.2f} a {unit}.")
            alts = ja[:3]
        warnings = a["warnings"][:1] if a.get("nti") else []
        if a.get("nti"):
            parts.append(warnings[0])
        if a.get("banned_fdc"):
            warnings.append("This combination is on India's banned list — ask your doctor about it.")
            parts.append(warnings[-1])
    elif r.verified:
        parts.append("I don't have a cheaper same-salt option listed for it yet; a Jan Aushadhi Kendra can check.")
    mem = await _memory_line(db, cu, r)
    if mem:
        parts.append(mem)
    if not r.verified:
        parts.append("Before switching to another brand, show the strip to a pharmacist.")
    else:
        parts.append("Ask your doctor or pharmacist before switching brands.")
    return {"text": " ".join(parts), "resolved": r.as_dict(), "alternatives": alts, "warnings": warnings}


async def _memory_line(db: AsyncSession, cu: CurrentUser | None, r: Resolved) -> str:
    """How a medicine with the same salt went for this person before (their own words, from memory)."""
    if cu is None:
        return ""
    generics = {c.get("generic", "") for c in r.composition}
    names = {r.query.lower(), *generics}
    rows = list(await db.scalars(select(HealthFact).where(HealthFact.user_id == cu.id, HealthFact.kind == "medicine_response")
                                 .order_by(HealthFact.observed_at.desc()).limit(20)))
    for f in rows:
        if any(n and (n in f.name or f.name in n) for n in names):
            v = json.loads(cu.cipher.dec(f.value_enc))
            when = f.observed_at.strftime("%B")
            note = f" ({v['note']})" if v.get("note") else ""
            return f"Last time you took {f.name} (in {when}), {EFFECT_EN.get(v.get('effect'), v.get('effect', ''))}{note}."
    return ""
