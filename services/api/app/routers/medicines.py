from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.llm.orchestrator import LANG_NAMES
from app.llm.providers import ProviderUnavailable, clinical_chat
from app.medicine import openfda, service
from app.medicine.inn import is_nti
from app.medicine.parse import parse_composition
from app.models import Allergy, Medication, PatientProfile, Product
from app.safety.disclaimer import disclaimer
from app.security.auth import CurrentUser, current_user

router = APIRouter(prefix="/medicines", tags=["medicines"])

# AGS Beers Criteria® (2023) — a few high-signal examples relevant to Indian OTC/Rx use. Not exhaustive.
BEERS_65 = {
    "chlorpheniramine": "First-generation antihistamine: confusion, constipation, falls in older adults.",
    "chlorphenamine": "First-generation antihistamine: confusion, constipation, falls in older adults.",
    "diphenhydramine": "First-generation antihistamine: confusion, falls in older adults.",
    "promethazine": "First-generation antihistamine: confusion, falls in older adults.",
    "hydroxyzine": "First-generation antihistamine: confusion, falls in older adults.",
    "glibenclamide": "Long-acting sulfonylurea: higher risk of prolonged low blood sugar.",
    "glimepiride": "Sulfonylurea: risk of low blood sugar in older adults.",
    "diazepam": "Benzodiazepine: falls, fractures, confusion.",
    "alprazolam": "Benzodiazepine: falls, fractures, confusion.",
    "clonazepam": "Benzodiazepine: falls, fractures, confusion.",
    "amitriptyline": "Strongly anticholinergic: confusion, constipation, low BP on standing.",
    "dicyclomine": "Anticholinergic antispasmodic: confusion, constipation.",
    "metoclopramide": "Risk of movement disorders; avoid unless gastroparesis.",
    "diclofenac": "NSAID: stomach bleeding and kidney risk; avoid long-term use.",
    "ibuprofen": "NSAID: stomach bleeding and kidney risk; avoid long-term use.",
    "aceclofenac": "NSAID: stomach bleeding and kidney risk; avoid long-term use.",
    "etoricoxib": "NSAID: kidney and heart risk; avoid long-term use.",
    "aspirin": "For primary prevention in older adults, bleeding risk may outweigh benefit — follow your doctor.",
}
# Common cross-sensitivity groups for allergy checks
ALLERGY_GROUPS = {
    "penicillin": {"amoxicillin", "ampicillin", "cloxacillin", "piperacillin", "benzathine penicillin", "penicillin"},
    "sulfa": {"sulfamethoxazole", "sulfasalazine", "sulfadiazine"},
    "nsaid": {"ibuprofen", "diclofenac", "aceclofenac", "naproxen", "aspirin", "etoricoxib", "mefenamic acid",
              "piroxicam", "ketorolac", "nimesulide"},
    "cephalosporin": {"cefalexin", "cefixime", "cefuroxime", "ceftriaxone", "cefpodoxime", "cefadroxil"},
}


@router.get("/search")
async def search(q: str = Query(min_length=2, max_length=120), db: AsyncSession = Depends(get_db)):
    return {"query": q, "results": await service.search(db, q)}


@router.get("/{pid}")
async def get(pid: str, db: AsyncSession = Depends(get_db)):
    p = await db.get(Product, pid)
    if p is None:
        raise HTTPException(404, "Not found")
    return await service.product_out(db, p)


@router.get("/{pid}/alternatives")
async def alternatives(pid: str, db: AsyncSession = Depends(get_db)):
    p = await db.get(Product, pid)
    if p is None:
        raise HTTPException(404, "Not found")
    return {**await service.alternatives(db, p), "disclaimer": disclaimer("en")}


class CompositionQuery(BaseModel):
    text: str = Field(min_length=2, max_length=300, description="e.g. 'Paracetamol 650 mg tablet'")


@router.post("/alternatives/by-composition")
async def alternatives_by_composition(body: CompositionQuery, db: AsyncSession = Depends(get_db)):
    comp = parse_composition(body.text)
    if not comp.ingredients or not all(i.strength for i in comp.ingredients):
        raise HTTPException(422, "Please include each medicine's strength, e.g. 'Paracetamol 650 mg tablet'")
    rows = [p for p in await db.scalars(select(Product).where(Product.composition_key == comp.key))
            if not comp.form or p.form == comp.form]
    items = [await service.product_out(db, p) for p in rows if p.release_type == comp.release]
    items.sort(key=lambda it: ((it["price"] or {}).get("per_unit_inr") is None,
                               (it["price"] or {}).get("per_unit_inr") or 0))
    return {"composition": comp.as_list(), "form": comp.form, "release_type": comp.release, "alternatives": items,
            "nti": [i.generic for i in comp.ingredients if is_nti(i.generic)]}


def _age(dob: str | None) -> float | None:
    try:
        return (date.today() - date.fromisoformat(dob)).days / 365.25 if dob else None
    except ValueError:
        return None


@router.get("/guidance/{generic}")
async def guidance(generic: str, lang: str = "en", explain: bool = False,
                   cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Usage, timing, side effects and contraindications, personalised to the user's profile."""
    generic = parse_composition(generic).ingredients[0].generic if parse_composition(generic).ingredients else generic.lower()
    label = await openfda.label(db, generic)
    alerts: list[dict] = []

    # allergy cross-check
    allergies = [cu.cipher.dec(a.substance_enc).lower() for a in await db.scalars(
        select(Allergy).where(Allergy.user_id == cu.id))]
    for a in allergies:
        group = next((g for g, members in ALLERGY_GROUPS.items() if a in members or a.startswith(g)), None)
        if a == generic or (group and generic in ALLERGY_GROUPS[group]):
            alerts.append({"level": "high", "type": "allergy",
                           "text": f"You have recorded an allergy to '{a}'. Do not take {generic} unless your doctor "
                                   f"confirms it is safe."})
        if group == "penicillin" and generic in ALLERGY_GROUPS["cephalosporin"]:
            alerts.append({"level": "moderate", "type": "allergy",
                           "text": "Penicillin allergy: a small cross-reaction risk exists with cephalosporins. "
                                   "Tell your doctor about your allergy."})

    p = await db.get(PatientProfile, cu.id)
    age = _age(cu.cipher.dec(p.dob_enc)) if p else None
    if age is not None and age >= 65 and generic in BEERS_65:
        alerts.append({"level": "moderate", "type": "older_adult", "text": BEERS_65[generic],
                       "source": "AGS Beers Criteria (2023)"})
    if p and p.pregnancy_status in ("pregnant", "possibly_pregnant"):
        alerts.append({"level": "high", "type": "pregnancy",
                       "text": "You are or may be pregnant — confirm with your doctor that this medicine is safe in "
                               "pregnancy before taking it."})
    if is_nti(generic):
        alerts.append({"level": "moderate", "type": "nti",
                       "text": "Narrow-therapeutic-index medicine: take exactly as prescribed, do not switch brands, "
                               "and keep scheduled blood tests."})

    current = []
    for m in await db.scalars(select(Medication).where(Medication.user_id == cu.id, Medication.status == "active")):
        comp = parse_composition(cu.cipher.dec(m.name_enc))
        current += [i.generic for i in comp.ingredients]
    ddi = await service.interactions(db, [generic, *current])
    for d in ddi:
        alerts.append({"level": "high" if d["severity"] == "Major" else "moderate", "type": "interaction",
                       "text": f"Interaction between {d['a']} and {d['b']} ({d['severity']}). Ask your doctor or "
                               f"pharmacist.", "source": d["source"]})

    out = {"generic": generic, "label": label, "alerts": alerts, "disclaimer": disclaimer(lang),
           "note": None if label else "No openFDA label found for this molecule; ask your pharmacist for the "
                                      "package insert."}
    if explain and label:
        sections = {k: v for k, v in label.items() if k in
                    ("indications", "dosage_and_administration", "contraindications", "warnings",
                     "adverse_reactions", "patient_information", "storage")}
        msgs = [{"role": "system", "content":
                 "You explain medicine labels to patients in India in simple words. Use ONLY the label text given. "
                 "Never give a dose number or tell the user to change a dose — say 'take as prescribed by your "
                 f"doctor'. Answer in {LANG_NAMES.get(lang, 'English')} as JSON."},
                {"role": "user", "content": f"Medicine: {generic}\nLABEL: {sections}"}]
        schema = {"type": "object", "properties": {
            "what_it_is_for": {"type": "string"}, "how_to_take": {"type": "array", "items": {"type": "string"}},
            "with_food": {"type": "string"}, "common_side_effects": {"type": "array", "items": {"type": "string"}},
            "serious_side_effects_see_doctor": {"type": "array", "items": {"type": "string"}},
            "do_not_take_if": {"type": "array", "items": {"type": "string"}},
            "storage": {"type": "string"}},
            "required": ["what_it_is_for", "how_to_take", "common_side_effects", "serious_side_effects_see_doctor",
                         "do_not_take_if"]}
        try:
            out["explanation"] = (await clinical_chat(msgs, schema=schema)).json()
        except (ProviderUnavailable, ValueError) as e:
            out["explanation_error"] = str(e)
    return out


class InteractionQuery(BaseModel):
    medicines: list[str] = Field(min_length=1, max_length=20)


@router.post("/interactions")
async def interactions(body: InteractionQuery, db: AsyncSession = Depends(get_db)):
    generics = []
    for m in body.medicines:
        comp = parse_composition(m)
        generics += [i.generic for i in comp.ingredients] or [m.lower()]
    return {"generics": sorted(set(generics)), "interactions": await service.interactions(db, generics),
            "banned_fdc": await service.banned_check(db, generics),
            "note": "Absence of a listed interaction does not guarantee safety — confirm with a pharmacist."}
