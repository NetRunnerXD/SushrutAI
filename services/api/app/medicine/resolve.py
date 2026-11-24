"""Brand or medicine name → its salt (active composition), even when the brand name does not contain it.

In order, and saying which one answered:
1. `catalogue`  — the drug master (Jan Aushadhi generics and any loaded brand list): a verified composition;
2. `printed`    — a composition written next to the name (on the strip / box, or "Tab Dolo 650 (Paracetamol)"
                  on a prescription), read with the composition parser;
3. `model`      — the model's own knowledge of Indian brands, marked UNVERIFIED: shown with "please check the
                  strip" and never used to say two medicines are the same without the person confirming.
Never guesses silently.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.medicine import service
from app.medicine.parse import parse_composition
from app.models import Product

_PAREN = re.compile(r"\(([^)]{3,120})\)")
_SALT_SCHEMA = {
    "type": "object",
    "properties": {
        "known": {"type": "boolean"},
        "ingredients": {"type": "array", "maxItems": 4, "items": {"type": "object", "properties": {
            "generic": {"type": "string"}, "strength": {"type": "number"}, "unit": {"type": "string"}},
            "required": ["generic"]}},
        "form": {"type": "string"},
    },
    "required": ["known", "ingredients"],
}


@dataclass
class Resolved:
    query: str
    source: str                      # catalogue | printed | model | none
    verified: bool
    composition: list[dict] = field(default_factory=list)  # [{"generic": "paracetamol", "strength": 650, "unit": "mg"}]
    composition_key: str = ""
    product: dict | None = None      # the matching catalogue product, when there is one

    @property
    def salt_text(self) -> str:
        parts = []
        for c in self.composition:
            s = c.get("generic", "").capitalize()
            if c.get("strength"):
                s += f" {c['strength']:g} {c.get('unit') or 'mg'}"
            parts.append(s)
        return " + ".join(parts)

    def as_dict(self) -> dict:
        return {"query": self.query, "source": self.source, "verified": self.verified, "salt": self.salt_text,
                "composition": self.composition, "composition_key": self.composition_key, "product": self.product}


def _key(comp) -> str:
    return comp.key if comp and comp.ingredients else ""


async def resolve(db: AsyncSession, name: str, printed: str = "", *, use_model: bool = True) -> Resolved:
    from app.llm.intent import _generic_names

    name = name.strip()
    known = await _generic_names(db)  # real molecule names (the drug master), so "Dolo" is never taken for a salt
    first = (re.findall(r"[a-z]{3,}", name.lower()) or [""])[0]
    # 1. catalogue: a product whose name or salt contains the first word said ("paracetamol 650", a loaded brand)
    hits = await service.search(db, name, limit=3) if name else []
    if hits and hits[0].get("match_score", 0) >= 85 and first and (
            first in hits[0]["name"].lower() or any(first in c.get("generic", "") for c in hits[0]["composition"])):
        h = hits[0]
        return Resolved(name, "catalogue", True, h["composition"], h["composition_key"], h)
    # 2. composition written next to it (or the name is itself a salt: "Paracetamol 650")
    for text in [printed, *(_PAREN.findall(name)), name]:
        comp = parse_composition(text) if text else None
        if comp and comp.ingredients and all(i.generic in known for i in comp.ingredients):
            return Resolved(name, "printed", True,
                            [{"generic": i.generic, "strength": i.strength, "unit": i.unit} for i in comp.ingredients],
                            _key(comp), await _product_for(db, _key(comp)))
    # 3. the model's knowledge (unverified)
    if use_model and name:
        comp = await _model_salt(name)
        if comp:
            k = _key(parse_composition(" + ".join(f"{c['generic']} {c.get('strength') or ''}{c.get('unit') or ''}"
                                                  for c in comp)))
            return Resolved(name, "model", False, comp, k, await _product_for(db, k))
    return Resolved(name, "none", False)


async def _product_for(db: AsyncSession, key: str) -> dict | None:
    if not key:
        return None
    p = await db.scalar(select(Product).where(Product.composition_key == key).limit(1))
    return await service.product_out(db, p) if p else None


async def _model_salt(name: str) -> list[dict]:
    from app.config import get_settings
    from app.llm.providers import ProviderUnavailable, ollama

    try:
        res = await ollama().chat(get_settings().llm_model, [
            {"role": "system", "content": "You know medicines sold in India. Given a brand or product name, give its "
                                          "active ingredients (generic names, lower case) with strength. If you are not "
                                          "sure what this product is, set known=false and give no ingredients."},
            {"role": "user", "content": name[:120]}], schema=_SALT_SCHEMA, temperature=0.0, num_ctx=1024, num_predict=200)
        data = res.json()
    except (ProviderUnavailable, ValueError):
        return []
    if not isinstance(data, dict) or not data.get("known"):
        return []
    out = []
    for i in data.get("ingredients") or []:
        g = re.sub(r"[^a-z \-]", "", str(i.get("generic", "")).lower()).strip()
        if len(g) >= 4:
            out.append({"generic": g, "strength": i.get("strength"), "unit": (i.get("unit") or "mg").lower()})
    return out
