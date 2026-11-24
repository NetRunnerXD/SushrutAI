"""Third-party subject (family/friend) detection and relation-graph persistence."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ClinicalFact
from app.security.auth import CurrentUser

_FAMILY_RELATIONS: tuple[tuple[str, str | None, str], ...] = (
    (r"\b(?:mother|mom|mummy|maa|ammi|মা|माँ|मम्मी)\b", "female", "mother"),
    (r"\b(?:father|dad|daddy|papa|baba|abbu|বাবা|पापा|पिता)\b", "male", "father"),
    (r"\b(?:son|ছেলে|बेटा)\b", "male", "son"),
    (r"\b(?:daughter|মেয়ে|बेटी)\b", "female", "daughter"),
    (r"\b(?:wife|স্ত্রী|বউ|पत्नी|बीवी)\b", "female", "wife"),
    (r"\b(?:husband|স্বামী|বর|पति)\b", "male", "husband"),
    (r"\b(?:spouse|partner)\b", None, "spouse"),
    (r"\b(?:brother|ভাই|দাদা|भाई)\b", "male", "brother"),
    (r"\b(?:sister|বোন|দিদি|बहन)\b", "female", "sister"),
    (r"\b(?:grandmother|grandma|nani|dadi|ঠাকুমা|দিদা|दादी|नानी)\b", "female", "grandmother"),
    (r"\b(?:grandfather|grandpa|nana|dada|দাদু|ঠাকুরদা|दादा|नाना)\b", "male", "grandfather"),
    (r"\b(?:baby|infant|toddler|kid|child|বাচ্চা|শিশু|बच्चा|बच्चे)\b", None, "child"),
    (r"\b(?:relative|uncle|aunt|cousin|nephew|niece|আত্মীয়|रिश्तेदार)\b", None, "relative"),
)

_NON_FAMILY_RELATIONS: tuple[tuple[str, str | None, str], ...] = (
    (r"\b(?:friend|buddy|roommate|বন্ধু|दोस्त)\b", None, "friend"),
    (r"\b(?:colleague|coworker|सहकर्मी)\b", None, "colleague"),
    (r"\b(?:neighbor|neighbour|প্রতিবেশী|पड़ोसी)\b", None, "neighbor"),
)

_THIRD_PARTY_CUE = re.compile(
    r"(?i)\b(?:my|our|for\s+my|for\s+a|his|her|their|আমার|मेरा|मेरी|मेरे)\s+"
    r"(?:mother|mom|mummy|maa|ma|ammi|father|dad|daddy|papa|baba|abbu|son|daughter|wife|husband|spouse|partner|"
    r"brother|sister|grandmother|grandma|nani|dadi|grandfather|grandpa|nana|dada|baby|infant|toddler|kid|child|"
    r"relative|uncle|aunt|cousin|nephew|niece|friend|buddy|roommate|colleague|coworker|neighbor|neighbour|"
    r"মা|বাবা|ছেলে|মেয়ে|স্ত্রী|স্বামী|ভাই|বোন|বন্ধু|বাচ্চা|माँ|पापा|बेटा|बेटी|पत्नी|पति|भाई|बहन|दोस्त|बच्चा)\b"
)

_AGE_RE = re.compile(
    r"(?i)\b(\d{1,3})\s*(?:-?\s*(?:year|years|yr|yrs|y/o|yo)\b|\s*বছর|\s*साल)|"
    r"\b(?:age|aged|উমর|বয়স|उम्र)\s*(?:is|of|:)?\s*(\d{1,3})\b"
)


def parse_age(text: str) -> int | None:
    m = _AGE_RE.search(text or "")
    if m:
        raw = m.group(1) or m.group(2)
        if raw and raw.isdigit():
            val = int(raw)
            if 0 < val <= 120:
                return val
    # Standalone number fallback for short direct answers like "58"
    stripped = (text or "").strip()
    if stripped.isdigit():
        val = int(stripped)
        if 0 < val <= 120:
            return val
    return None


def parse_gender(text: str) -> str | None:
    low = (text or "").lower()
    if re.search(r"\b(?:female|woman|girl|lady|she|her|মহিলা|মেয়ে|নারী|महिला|लड़की|औरत|स्त्री)\b", low):
        return "female"
    if re.search(r"\b(?:male|man|boy|gentleman|he|him|পুরুষ|ছেলে|पुरुष|लड़का|आदमी)\b", low):
        return "male"
    if re.search(r"\b(?:non-?binary|other|transgender|অন্যান্য|अन्य)\b", low):
        return "other"
    return None


def detect_subject_relation(text: str) -> dict[str, Any]:
    """Detect whether the user is asking about a family member/friend and extract inline age/gender."""
    t = (text or "").strip()
    if not t or not _THIRD_PARTY_CUE.search(t):
        return {"is_third_party": False, "relation": None, "is_family": False, "age": None, "gender": None}

    age = parse_age(t)
    explicit_gender = parse_gender(t)

    best_match: tuple[int, str | None, str, bool] | None = None
    for pattern, default_gender, rel_name in _FAMILY_RELATIONS:
        for m in re.finditer(pattern, t, re.I):
            if best_match is None or m.end() > best_match[0]:
                best_match = (m.end(), default_gender, rel_name, True)

    for pattern, default_gender, rel_name in _NON_FAMILY_RELATIONS:
        for m in re.finditer(pattern, t, re.I):
            if best_match is None or m.end() > best_match[0]:
                best_match = (m.end(), default_gender, rel_name, False)

    if best_match is not None:
        _, default_gender, rel_name, is_fam = best_match
        return {
            "is_third_party": True,
            "relation": rel_name,
            "is_family": is_fam,
            "age": age,
            "gender": explicit_gender or default_gender,
        }

    return {"is_third_party": False, "relation": None, "is_family": False, "age": None, "gender": None}


async def upsert_subject_relation_fact(
    db: AsyncSession,
    cu: CurrentUser,
    *,
    encounter_id: str,
    relation: str,
    is_family: bool,
    age: int | None = None,
    gender: str | None = None,
    symptoms: list[str] | None = None,
    context_note: str | None = None,
    scope: str = "patient",
) -> ClinicalFact:
    """Create or update a ClinicalFact representing a family/friend relation node."""
    fact_key = f"relation:{relation.lower()}"
    existing = await db.scalar(
        select(ClinicalFact).where(
            ClinicalFact.user_id == cu.id,
            ClinicalFact.category == "subject_relation",
            ClinicalFact.fact_key == fact_key,
        ).order_by(ClinicalFact.created_at.desc())
    )
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    data: dict[str, Any] = {
        "relation": relation.lower(),
        "is_family": bool(is_family),
        "age": age,
        "gender": gender,
        "symptoms": [],
        "notes": [],
    }
    if existing is not None:
        try:
            prev = json.loads(cu.cipher.dec(existing.value_enc))
            if isinstance(prev, dict):
                data.update({k: v for k, v in prev.items() if v is not None})
        except Exception:
            pass
        if age is not None:
            data["age"] = age
        if gender is not None:
            data["gender"] = gender
        data["is_family"] = bool(is_family)
        existing_syms = list(data.get("symptoms") or [])
        for s in symptoms or []:
            if s and s not in existing_syms:
                existing_syms.append(s)
        data["symptoms"] = existing_syms[:12]
        existing_notes = list(data.get("notes") or [])
        if context_note and context_note.strip() and context_note.strip() not in existing_notes:
            existing_notes.append(context_note.strip()[:240])
        data["notes"] = existing_notes[:8]
        existing.value_enc = cu.cipher.enc(json.dumps(data))
        await db.flush()
        return existing

    if symptoms:
        data["symptoms"] = list(dict.fromkeys(s for s in symptoms if s))[:12]
    if context_note and context_note.strip():
        data["notes"] = [context_note.strip()[:240]]

    row = ClinicalFact(
        user_id=cu.id,
        encounter_id=encounter_id,
        scope=scope,
        category="subject_relation",
        fact_key=fact_key,
        value_enc=cu.cipher.enc(json.dumps(data)),
        verification_status="PATIENT_REPORTED",
        source_type="patient_chat",
        confidence=0.9,
        created_at=now,
    )
    db.add(row)
    await db.flush()
    return row


async def load_subject_relations(db: AsyncSession, cu: CurrentUser) -> list[dict[str, Any]]:
    """Load all persisted family/friend relation nodes for the current user AND linked household members."""
    from app.models import ClinicalSymptom, Condition, HouseholdMember, PatientProfile, User

    rows = list(await db.scalars(
        select(ClinicalFact).where(
            ClinicalFact.user_id == cu.id,
            ClinicalFact.category == "subject_relation",
        ).order_by(ClinicalFact.created_at.desc()).limit(16)
    ))
    by_rel: dict[str, dict[str, Any]] = {}
    for r in rows:
        try:
            val = json.loads(cu.cipher.dec(r.value_enc))
            if isinstance(val, dict) and val.get("relation"):
                rel_k = str(val["relation"]).lower()
                if rel_k not in by_rel:
                    by_rel[rel_k] = val
        except Exception:
            continue

    # Also pull separate profiles from linked HouseholdMembers under the same account
    # so any family member can retrieve information about any other family member.
    try:
        links = list(await db.scalars(select(HouseholdMember).where(HouseholdMember.user_id == cu.account_id)))
        for lk in links:
            rel_k = (lk.relation or "relative").lower()
            if lk.member_id == cu.id:
                continue
            member_user = await db.get(User, lk.member_id)
            if member_user is None:
                continue
            syms = list(await db.scalars(
                select(ClinicalSymptom).where(ClinicalSymptom.user_id == lk.member_id, ClinicalSymptom.status == "active").limit(8)
            ))
            conds = list(await db.scalars(
                select(Condition).where(Condition.user_id == lk.member_id, Condition.status == "active").limit(6)
            ))
            prof = await db.get(PatientProfile, lk.member_id)
            entry = by_rel.get(rel_k, {
                "relation": rel_k,
                "is_family": True,
                "age": None,
                "gender": getattr(prof, "sex", None) if prof else None,
                "symptoms": [],
                "notes": [],
            })
            # ClinicalSymptom rows on member_id have display_name_enc encrypted with member's DEK;
            # include count/status & condition awareness cleanly without failing on DEK mismatch.
            if conds and not entry.get("notes"):
                entry["notes"] = [f"{len(conds)} active condition(s) on file"]
            if syms and not entry.get("symptoms"):
                entry["notes"] = list(entry.get("notes") or []) + [f"{len(syms)} active symptom(s) tracked"]
            by_rel[rel_k] = entry
    except Exception:
        pass

    return list(by_rel.values())


def detect_family_query_targets(text: str) -> list[str]:
    """Identify which family relation(s) a user is asking about."""
    t = (text or "").strip()
    if not t:
        return []
    found: list[str] = []
    for pattern, _g, rel_name in _FAMILY_RELATIONS + _NON_FAMILY_RELATIONS:
        if re.search(pattern, t, re.I) and rel_name not in found:
            found.append(rel_name)
    return found


async def retrieve_family_member_context(db: AsyncSession, cu: CurrentUser, query_text: str) -> list[str]:
    """Retrieve separate symptom/profile context for queried family members & cross-family correlations."""
    relations = await load_subject_relations(db, cu)
    if not relations:
        return []
    targets = detect_family_query_targets(query_text)
    out: list[str] = []

    # 1. Direct retrieval for the asked-about family member(s)
    for rel in relations:
        rname = str(rel.get("relation") or "").lower()
        if targets and rname not in targets:
            continue
        bits = [f"Family/Subject profile ({rname})"]
        if rel.get("age") is not None:
            bits.append(f"age {rel['age']}y")
        if rel.get("gender"):
            bits.append(str(rel["gender"]))
        if rel.get("symptoms"):
            bits.append("symptoms: " + ", ".join(str(s) for s in rel["symptoms"][:6]))
        if rel.get("notes"):
            bits.append("notes: " + "; ".join(str(n) for n in rel["notes"][:3]))
        out.append(" | ".join(bits))

    # 2. Cross-family symptom correlation (shared symptoms across family members)
    sym_owners: dict[str, list[str]] = {}
    for rel in relations:
        rname = str(rel.get("relation") or "").lower()
        for s in (rel.get("symptoms") or []):
            sl = str(s).strip().lower()
            if sl:
                sym_owners.setdefault(sl, []).append(rname)
    for sym, owners in sym_owners.items():
        uniq = list(dict.fromkeys(owners))
        if len(uniq) >= 2:
            out.append(f"Cross-family correlation: '{sym}' reported across {', '.join(uniq)} (possible shared exposure or familial pattern).")

    return out[:6]
