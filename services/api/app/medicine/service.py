from __future__ import annotations

import re
from dataclasses import dataclass

import jellyfish
from rapidfuzz import fuzz, process
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.medicine.inn import is_nti
from app.medicine.parse import parse_composition
from app.models import BannedFdc, Interaction, Price, Product


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower()).strip()


# OCR confusions typical of handwritten / printed Rx
_OCR_FOLD = str.maketrans({"0": "o", "1": "l", "5": "s", "8": "b", "|": "l"})


@dataclass
class Match:
    product: Product
    score: float


class ProductIndex:
    """In-memory fuzzy index over product names (rebuilt after ingestion)."""

    def __init__(self) -> None:
        self.names: list[str] = []
        self.ids: list[str] = []
        self.phon: dict[str, list[int]] = {}
        self.loaded = False

    async def load(self, db: AsyncSession) -> None:
        rows = (await db.execute(select(Product.id, Product.name_norm))).all()
        self.ids = [r[0] for r in rows]
        self.names = [r[1] for r in rows]
        self.phon = {}
        for i, n in enumerate(self.names):
            first = n.split(" ")[0] if n else ""
            if first:
                self.phon.setdefault(jellyfish.metaphone(first), []).append(i)
        self.loaded = True

    def search(self, q: str, limit: int = 10) -> list[tuple[str, float]]:
        qn = norm(q)
        if not qn or not self.names:
            return []
        scored: dict[int, float] = {}
        for _, score, idx in process.extract(qn, self.names, scorer=fuzz.WRatio, limit=limit * 3):
            scored[idx] = max(scored.get(idx, 0), score)
        folded = qn.translate(_OCR_FOLD)
        if folded != qn:
            for _, score, idx in process.extract(folded, self.names, scorer=fuzz.WRatio, limit=limit):
                scored[idx] = max(scored.get(idx, 0), score * 0.97)
        for idx in self.phon.get(jellyfish.metaphone(qn.split(" ")[0]), [])[:50]:
            scored[idx] = max(scored.get(idx, 0), 0.6 * fuzz.WRatio(qn, self.names[idx]) + 35)
        best = sorted(scored.items(), key=lambda kv: -kv[1])[:limit]
        return [(self.ids[i], round(s, 1)) for i, s in best]


INDEX = ProductIndex()


async def latest_price(db: AsyncSession, product_id: str) -> Price | None:
    return (await db.scalars(select(Price).where(Price.product_id == product_id)
                             .order_by(Price.observed_on.desc()))).first()


def _unit_price(p: Product, pr: Price | None) -> float | None:
    if pr is None:
        return None
    if pr.per_unit_inr:
        return pr.per_unit_inr
    if pr.mrp_inr and p.pack_size and p.pack_unit == "units":
        return round(pr.mrp_inr / p.pack_size, 2)
    return None


async def product_out(db: AsyncSession, p: Product) -> dict:
    pr = await latest_price(db, p.id)
    return {
        "id": p.id, "name": p.name, "manufacturer": p.manufacturer, "composition": p.composition,
        "composition_key": p.composition_key, "form": p.form, "release_type": p.release_type,
        "pack_size": p.pack_size, "pack_unit": p.pack_unit, "category": p.category,
        "is_janaushadhi": p.is_janaushadhi, "source": p.source, "verified": p.verified,
        "price": None if pr is None else {
            "mrp_inr": pr.mrp_inr, "per_unit_inr": _unit_price(p, pr), "ceiling_price_inr": pr.ceiling_price_inr,
            "source": pr.source, "source_url": pr.source_url, "observed_on": pr.observed_on},
    }


async def search(db: AsyncSession, q: str, limit: int = 10) -> list[dict]:
    if not INDEX.loaded:
        await INDEX.load(db)
    out = []
    for pid, score in INDEX.search(q, limit):
        p = await db.get(Product, pid)
        if p:
            out.append({**await product_out(db, p), "match_score": score})
    # A typed composition ("paracetamol 650", "metformin 500 SR") ranks exact-composition products first.
    # A bare number after a molecule name is read as mg, as people say it.
    q_units = re.sub(r"(?i)(\d+(?:\.\d+)?)(?!\s*(?:mg|mcg|µg|g|gm|iu|ml|%|units?|\d))", r"\1mg", q)
    comp = parse_composition(q_units)
    if comp.ingredients and all(i.strength for i in comp.ingredients):
        same = list(await db.scalars(select(Product).where(Product.composition_key == comp.key)))
        exact_ids = {p.id for p in same}
        out = [o for o in out if o["id"] not in exact_ids]
        for p in same:
            bonus = 100.0 + (2 if comp.release == p.release_type else 0) + (1 if not comp.form or comp.form == p.form else 0)
            out.append({**await product_out(db, p), "match_score": bonus})
    return sorted(out, key=lambda o: -o["match_score"])[:limit]


async def alternatives(db: AsyncSession, product: Product) -> dict:
    """Same composition (molecules + strengths), same dosage form and release type — sorted by unit price."""
    q = select(Product).where(Product.composition_key == product.composition_key, Product.id != product.id)
    rows = [p for p in await db.scalars(q)
            if (p.form or "") == (product.form or "") and p.release_type == product.release_type]
    items = [await product_out(db, p) for p in rows]
    base = await product_out(db, product)
    base_unit = (base["price"] or {}).get("per_unit_inr")
    for it in items:
        u = (it["price"] or {}).get("per_unit_inr")
        it["savings_pct"] = round(100 * (base_unit - u) / base_unit, 1) if (base_unit and u) else None
    items.sort(key=lambda it: ((it["price"] or {}).get("per_unit_inr") is None,
                               (it["price"] or {}).get("per_unit_inr") or 0))
    generics = [c["generic"] for c in product.composition]
    nti = [g for g in generics if is_nti(g)]
    warnings = ["Generic medicines with the same composition are expected to work the same, but ask your "
                "doctor or pharmacist before switching."]
    if nti:
        warnings.insert(0, f"{', '.join(nti)} is a narrow-therapeutic-index medicine: do NOT switch brand or "
                           f"manufacturer without your doctor's advice — small differences can matter.")
    banned = await banned_check(db, generics)
    return {"product": base, "alternatives": items, "janaushadhi": [i for i in items if i["is_janaushadhi"]],
            "nti": nti, "banned_fdc": banned, "warnings": warnings}


async def banned_check(db: AsyncSession, generics: list[str]) -> list[dict]:
    """Exact banned combination (same set of molecules) or any individually prohibited molecule."""
    gs = sorted({g for g in generics if g})
    if not gs:
        return []
    keys = {"+".join(gs), *gs}
    rows = await db.scalars(select(BannedFdc).where(BannedFdc.composition_key.in_(keys)))
    return [{"description": b.description, "notification": b.notification_ref, "status": b.status,
             "status_note": b.status_note, "source_url": b.source_url} for b in rows]


async def interactions(db: AsyncSession, generics: list[str]) -> list[dict]:
    gs = sorted({g.lower() for g in generics})
    if len(gs) < 2:
        return []
    rows = await db.scalars(select(Interaction).where(
        or_(Interaction.generic_a.in_(gs), Interaction.generic_b.in_(gs))))
    order = {"Major": 0, "Moderate": 1, "Minor": 2, "Unknown": 3}
    out = [{"a": r.generic_a, "b": r.generic_b, "severity": r.severity, "source": r.source}
           for r in rows if r.generic_a in gs and r.generic_b in gs]
    uniq = {tuple(sorted((o["a"], o["b"]))): o for o in out}
    return sorted(uniq.values(), key=lambda o: order.get(o["severity"], 9))
