"""Safety checks when a medicine is added, and cheaper refills.

Medicines are often entered by brand ("Telma 40", "Dolo 650"); the drug master holds Jan Aushadhi generics, so
common Indian brands are mapped to their generics here (curated; extend as needed). Allergy checks match the
substance and its drug class (an allergy to penicillin also covers amoxicillin). Interaction pairs come from
DDInter via medicine.service.interactions. This is a warning to talk to a doctor or pharmacist, never advice.
"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.medicine import service
from app.medicine.parse import parse_composition
from app.models import Allergy, Medication, Product
from app.security.auth import CurrentUser

# Common Indian brands -> generic names (lower case, as in drug_generics.inn_name)
BRANDS: dict[str, list[str]] = {
    "dolo": ["paracetamol"], "crocin": ["paracetamol"], "calpol": ["paracetamol"], "metacin": ["paracetamol"],
    "combiflam": ["ibuprofen", "paracetamol"], "brufen": ["ibuprofen"], "disprin": ["aspirin"], "ecosprin": ["aspirin"],
    "augmentin": ["amoxicillin", "clavulanic acid"], "moxikind": ["amoxicillin"], "mox": ["amoxicillin"],
    "azithral": ["azithromycin"], "azee": ["azithromycin"], "taxim": ["cefixime"], "zifi": ["cefixime"],
    "ciplox": ["ciprofloxacin"], "norflox": ["norfloxacin"], "flagyl": ["metronidazole"], "metrogyl": ["metronidazole"],
    "septran": ["sulfamethoxazole", "trimethoprim"], "bactrim": ["sulfamethoxazole", "trimethoprim"],
    "telma": ["telmisartan"], "amlong": ["amlodipine"], "stamlo": ["amlodipine"], "losar": ["losartan"],
    "glycomet": ["metformin"], "gluconorm": ["metformin"], "janumet": ["sitagliptin", "metformin"],
    "amaryl": ["glimepiride"], "thyronorm": ["levothyroxine"], "eltroxin": ["levothyroxine"],
    "pan": ["pantoprazole"], "pantocid": ["pantoprazole"], "omez": ["omeprazole"], "rantac": ["ranitidine"],
    "atorva": ["atorvastatin"], "lipitor": ["atorvastatin"], "rosuvas": ["rosuvastatin"],
    "allegra": ["fexofenadine"], "cetzine": ["cetirizine"], "montair": ["montelukast"], "asthalin": ["salbutamol"],
    "voveran": ["diclofenac"], "zerodol": ["aceclofenac"], "nise": ["nimesulide"], "meftal": ["mefenamic acid"],
    "warf": ["warfarin"], "clopilet": ["clopidogrel"], "deplatt": ["clopidogrel"], "shelcal": ["calcium carbonate"],
}

# Allergy word -> drug class members that share the allergy risk
ALLERGY_CLASSES: dict[str, set[str]] = {
    "penicillin": {"penicillin", "amoxicillin", "ampicillin", "cloxacillin", "piperacillin", "benzathine penicillin"},
    "sulfa": {"sulfamethoxazole", "sulfasalazine", "sulfadiazine"},
    "sulpha": {"sulfamethoxazole", "sulfasalazine", "sulfadiazine"},
    "nsaid": {"ibuprofen", "diclofenac", "aceclofenac", "naproxen", "aspirin", "ketorolac", "mefenamic acid",
              "etoricoxib", "nimesulide", "piroxicam", "indomethacin"},
    "aspirin": {"aspirin"},
    "cephalosporin": {"cefixime", "cefuroxime", "ceftriaxone", "cefpodoxime", "cephalexin", "cefadroxil"},
}


_ALL_CLASS_MEMBERS = set().union(*ALLERGY_CLASSES.values()) | {g for gs in BRANDS.values() for g in gs}


async def generics_for(db: AsyncSession, name: str, product_id: str | None = None) -> list[str]:
    """Generic names in a medicine, from its catalogue product, its typed composition, or a known brand."""
    if product_id and (p := await db.get(Product, product_id)):
        gs = [c.get("generic", "").lower() for c in p.composition or [] if c.get("generic")]
        if gs:
            return gs
    words = re.findall(r"[a-z]+", name.lower())
    for w in words:  # a known brand first: the composition parser reads any word ("augmentin") as a generic
        if w in BRANDS:
            return BRANDS[w]
    from app.llm.intent import _generic_names
    known = await _generic_names(db)
    parsed = [i.generic.lower() for i in parse_composition(name).ingredients]
    real = [g for g in parsed if g in known or g in _ALL_CLASS_MEMBERS]
    return real or [w for w in words if w in known or w in _ALL_CLASS_MEMBERS]


async def check_new_medicine(db: AsyncSession, cu: CurrentUser, name: str, product_id: str | None,
                             exclude_id: str | None = None) -> list[dict]:
    """Warnings for adding this medicine: allergy match and interactions with the person's current medicines."""
    new = set(await generics_for(db, name, product_id))
    if not new:
        return []
    out: list[dict] = []
    for a in await db.scalars(select(Allergy).where(Allergy.user_id == cu.id)):
        sub = (cu.cipher.dec(a.substance_enc) or "").lower()
        risky = set()
        for word, members in ALLERGY_CLASSES.items():
            if word in sub:
                risky |= members
        hit = sorted(g for g in new if g in risky or g in sub or (len(sub) > 4 and sub in g))
        if hit:
            out.append({"kind": "allergy", "level": "high", "allergy": cu.cipher.dec(a.substance_enc), "generics": hit})
    current: dict[str, str] = {}
    for m in await db.scalars(select(Medication).where(Medication.user_id == cu.id, Medication.status == "active")):
        if m.id == exclude_id:
            continue
        nm = cu.cipher.dec(m.name_enc)
        for g in await generics_for(db, nm, m.product_id):
            current.setdefault(g, nm)
    if current:
        for it in await service.interactions(db, list(new | set(current))):
            a, b = it["a"], it["b"]
            if (a in new) == (b in new):  # only pairs between the new medicine and an existing one
                continue
            if it["severity"] not in ("Major", "Moderate"):
                continue  # Minor / Unknown DDInter pairs would alarm people without a clear reason to act
            other = b if a in new else a
            out.append({"kind": "interaction", "level": "high" if it["severity"] == "Major" else "medium",
                        "severity": it["severity"], "with": current.get(other, other),
                        "generics": sorted({a, b}), "source": it["source"]})
    return out


async def cheapest_option(db: AsyncSession, name: str, product_id: str | None) -> dict | None:
    """The cheapest catalogue product with the same composition (usually a Jan Aushadhi generic)."""
    key = None
    if product_id and (p := await db.get(Product, product_id)):
        key = p.composition_key
    else:
        comp = parse_composition(name)
        if comp.ingredients and all(i.strength for i in comp.ingredients):
            key = comp.key
    if not key:
        return None
    best = None
    for p in await db.scalars(select(Product).where(Product.composition_key == key)):
        out = await service.product_out(db, p)
        price = (out["price"] or {}).get("per_unit_inr")
        if price is not None and (best is None or price < best["per_unit_inr"]):
            best = {"id": p.id, "name": p.name, "per_unit_inr": price, "is_janaushadhi": p.is_janaushadhi}
    return best
