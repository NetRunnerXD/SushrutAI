"""openFDA drug label API (public domain, free; key optional) — side effects, contraindications,
administration and interactions for the same molecule. Responses are cached in drug_monograph_cache.
Docs: https://open.fda.gov/apis/drug/label/
"""
from __future__ import annotations

import re
from datetime import timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.medicine.inn import to_usan
from app.models import MonographCache
from app.models import _now as utcnow

URL = "https://api.fda.gov/drug/label.json"
SECTIONS = {
    "indications": ["indications_and_usage", "purpose"],
    "dosage_and_administration": ["dosage_and_administration", "directions"],
    "contraindications": ["contraindications", "do_not_use"],
    "warnings": ["boxed_warning", "warnings_and_cautions", "warnings", "precautions", "ask_doctor", "stop_use"],
    "adverse_reactions": ["adverse_reactions"],
    "drug_interactions": ["drug_interactions", "ask_doctor_or_pharmacist"],
    "pregnancy": ["pregnancy", "pregnancy_or_breast_feeding", "use_in_specific_populations"],
    "patient_information": ["information_for_patients", "patient_medication_information", "spl_patient_package_insert"],
    "overdosage": ["overdosage"],
    "storage": ["storage_and_handling", "how_supplied"],
}
TTL = timedelta(days=30)


def _clean(s: str, limit: int = 1800) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"^\d+(\.\d+)*\s+[A-Z &]+\s+", "", s)  # leading "5 WARNINGS AND PRECAUTIONS"
    return s[:limit] + ("…" if len(s) > limit else "")


async def label(db: AsyncSession, generic: str) -> dict | None:
    generic = generic.lower()
    cached = (await db.scalars(select(MonographCache).where(MonographCache.generic == generic))).first()
    if cached and utcnow() - cached.fetched_at < TTL:
        return cached.payload or None
    params = {"search": f'openfda.generic_name:"{to_usan(generic)}"', "limit": 5}
    if key := get_settings().openfda_api_key:
        params["api_key"] = key
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(URL, params=params)
    except httpx.HTTPError:
        return cached.payload if cached else None
    if r.status_code == 404:
        payload: dict = {}
    else:
        r.raise_for_status()
        results = r.json().get("results", [])
        # Prefer single-ingredient prescription labels (most complete sections)
        results.sort(key=lambda x: (len(x.get("openfda", {}).get("generic_name", ["", ""])[0].split(" and ")),
                                    "adverse_reactions" not in x))
        payload = {}
        if results:
            best = results[0]
            for out_key, keys in SECTIONS.items():
                texts = [_clean(" ".join(best[k])) for k in keys if k in best]
                if texts:
                    payload[out_key] = texts[0] if len(texts) == 1 else " ".join(texts)[:2400]
            ofda = best.get("openfda", {})
            payload["_meta"] = {
                "source": "openFDA drug label (US FDA SPL, public domain)",
                "set_id": best.get("set_id"), "effective_time": best.get("effective_time"),
                "us_generic_name": (ofda.get("generic_name") or [None])[0],
                "url": f"https://dailymed.nlm.nih.gov/dailymed/lookup.cfm?setid={best.get('set_id')}",
            }
    if cached:
        cached.payload, cached.fetched_at = payload, utcnow()
    else:
        db.add(MonographCache(generic=generic, payload=payload, source="openfda"))
    await db.commit()
    return payload or None
