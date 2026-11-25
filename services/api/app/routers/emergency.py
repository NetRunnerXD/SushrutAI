"""Emergency help near the person: nearest hospitals / ambulance services with their direct numbers, and a short
spoken summary to play to the responder when the call connects.

- Places come from OpenStreetMap (Overpass API, free, no key). Coordinates are rounded to ~100 m before they
  leave this computer. When GOOGLE_PLACES_API_KEY is set, a missing phone number is looked up in Google Places.
- Calls are never placed by the app: the phone's dialler opens after a spoken preview and a clear yes.
- National numbers (108, 112) are always returned as the fallback.
- Offline mode: no lookup (the app shows the cached list from the last time, and the national numbers).
"""
from __future__ import annotations

import logging
import math

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db
from app.security.auth import CurrentUser, current_user
from app.security.settings import get_settings_row, server_offline

router = APIRouter(prefix="/emergency", tags=["emergency"])
log = logging.getLogger(__name__)

OVERPASS = "https://overpass-api.de/api/interpreter"
# public Overpass instances tried in turn: one busy or down server must not leave an emergency list empty
OVERPASS_MIRRORS = (OVERPASS, "https://overpass.private.coffee/api/interpreter", "https://overpass.kumi.systems/api/interpreter")
NATIONAL = [{"name": "Ambulance (108)", "phone": "108", "kind": "national"},
            {"name": "Emergency (112)", "phone": "112", "kind": "national"}]
RADII_M = (5000, 15000, 40000)


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _query(lat: float, lon: float, radius: int) -> str:
    around = f"(around:{radius},{lat},{lon})"
    return (f'[out:json][timeout:8];(nwr["amenity"="hospital"]{around};'
            f'nwr["healthcare"="hospital"]{around};'
            f'nwr["amenity"="clinic"]["emergency"="yes"]{around};'
            f'nwr["emergency"="ambulance_station"]{around};);out center tags 60;')


def _phone(tags: dict) -> str | None:
    for k in ("emergency:phone", "phone", "contact:phone", "contact:mobile", "mobile"):
        v = tags.get(k)
        if v:
            return v.split(";")[0].strip()
    return None


def parse_overpass(data: dict, lat: float, lon: float) -> list[dict]:
    out, seen = [], set()
    for el in data.get("elements", []):
        tags = el.get("tags") or {}
        name = tags.get("name:en") or tags.get("name")
        if not name or name.lower() in seen:
            continue
        la = el.get("lat") or (el.get("center") or {}).get("lat")
        lo = el.get("lon") or (el.get("center") or {}).get("lon")
        if la is None or lo is None:
            continue
        seen.add(name.lower())
        kind = "ambulance" if tags.get("emergency") == "ambulance_station" else \
            "clinic" if tags.get("amenity") == "clinic" else "hospital"
        out.append({"name": name, "kind": kind, "phone": _phone(tags), "lat": la, "lon": lo,
                    "distance_km": round(_km(lat, lon, la, lo), 1),
                    "emergency_dept": tags.get("emergency") == "yes",
                    "address": ", ".join(x for x in (tags.get("addr:street"), tags.get("addr:city")) if x) or None,
                    "source": "openstreetmap"})
    # nearest first; places with a phone number and an emergency department rank a little higher
    out.sort(key=lambda p: p["distance_km"] - (0.5 if p["phone"] else 0) - (0.5 if p["emergency_dept"] else 0))
    return out


async def _google_phone(client: httpx.AsyncClient, key: str, name: str, lat: float, lon: float) -> str | None:
    r = await client.post("https://places.googleapis.com/v1/places:searchText",
                          headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": "places.nationalPhoneNumber,places.displayName"},
                          json={"textQuery": name, "maxResultCount": 1,
                                "locationBias": {"circle": {"center": {"latitude": lat, "longitude": lon}, "radius": 3000}}})
    if r.status_code >= 400:
        raise httpx.HTTPStatusError(f"Google Places {r.status_code}", request=r.request, response=r)
    places = r.json().get("places") or []
    return (places[0].get("nationalPhoneNumber") or "").replace(" ", "") or None if places else None


@router.get("/nearby")
async def nearby(lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180),
                 cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    s = await get_settings_row(db, cu.account_id)
    await db.commit()
    if server_offline() or s.offline_mode:
        raise HTTPException(503, "Offline mode is on: nearby hospitals cannot be looked up. Call 108 or 112.")
    lat, lon = round(lat, 3), round(lon, 3)  # ~100 m: enough to find hospitals, less precise than the GPS fix
    places: list[dict] = []
    reached = False
    # 8 s per server: in an emergency a slow map service must not hold up the list (108 / 112 are always shown)
    async with httpx.AsyncClient(timeout=8, headers={"User-Agent": "SushrutAI/1.0 (health app)"}) as c:
        for radius in RADII_M:
            data = None
            for url in OVERPASS_MIRRORS:
                try:
                    r = await c.post(url, data={"data": _query(lat, lon, radius)})
                    r.raise_for_status()
                    data = r.json()
                    break
                except (httpx.HTTPError, ValueError) as e:
                    log.info("overpass %s failed: %s", url.split("/")[2], type(e).__name__)
            if data is None:
                break  # no map service reachable: say so (the app keeps its last list and the national numbers)
            reached = True
            places = parse_overpass(data, lat, lon)
            if len(places) >= 3:
                break
        phones_incomplete = False
        key = get_settings().google_places_api_key
        if key:
            for p in places[:5]:
                if not p["phone"]:
                    try:
                        p["phone"] = await _google_phone(c, key, p["name"], p["lat"], p["lon"])
                        if p["phone"]:
                            p["phone_source"] = "google_places"
                    except httpx.HTTPError:
                        phones_incomplete = True
    return {"places": places[:8], "national": NATIONAL, "lat": lat, "lon": lon, "unavailable": not reached,
            "phones_incomplete": phones_incomplete,
            "note": "From OpenStreetMap; phone numbers can be missing or out of date. 108 / 112 always work."}


@router.get("/summary")
async def responder_summary(symptoms: str = "", lat: float | None = None, lon: float | None = None,
                            cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """A 15-second summary to play on speaker to the ambulance / hospital: who, what is happening, what to know."""
    from app.routers.records import emergency_card

    card = await emergency_card(cu=cu, db=db)
    who = ", ".join(x for x in [f"{card['age']} years old" if card.get("age") else "", card.get("sex") or ""] if x)
    parts = [f"This is an emergency call made with the SushrutAI app. The patient is {who or 'an adult'}."]
    if symptoms.strip():
        parts.append(f"Symptoms now: {symptoms.strip()[:200]}.")
    if card.get("conditions"):
        parts.append("Known conditions: " + ", ".join(card["conditions"][:4]) + ".")
    if card.get("allergies"):
        parts.append("Allergies: " + ", ".join(card["allergies"][:4]) + ".")
    if card.get("medicines"):
        parts.append("Medicines: " + ", ".join(card["medicines"][:5]) + ".")
    if card.get("blood_group"):
        parts.append(f"Blood group {card['blood_group']}.")
    if lat is not None and lon is not None:
        parts.append(f"Location coordinates: {round(lat, 4)}, {round(lon, 4)}.")
    return {"text": " ".join(parts)}
