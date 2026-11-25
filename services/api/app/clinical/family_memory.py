"""Relationship-Aware Family Health Memory & Persistent Profile Engine.

Resolves English, Bengali, and Banglish family relationship mentions ("my father",
"amar baba", "amar ma", "amar dada", "amar dida", "amar bhai", "amar bon",
"amar chele", "amar meye", etc.) to the exact encrypted HouseholdMember profile,
never mixing records across family members.

Supports:
  - 15+ family members per account (MAX_MEMBERS = 20)
  - Clarification when multiple members share a relationship (e.g., two brothers)
  - Non-destructive vital history (always appends new readings with timestamp/source,
    preserving historical readings and computing comparisons)
  - Compact, patient-isolated memory context for non-repetitive questioning
  - Audit logging for family data changes
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.assistant.actions import parse_vitals
from app.clock import local_now
from app.db import scope_to
from app.models import (
    Allergy,
    AuditEvent,
    Condition,
    HouseholdMember,
    Medication,
    PatientProfile,
    User,
    Vital,
)
from app.security.auth import CurrentUser
from app.security.crypto import Cipher, new_wrapped_dek

MAX_FAMILY_MEMBERS = 20

FIRST_OPEN_PROMPT = {
    "bn": "আপনি কি নিজের জন্য জানতে চাইছেন, নাকি পরিবারের অন্য কারও জন্য?",
    "en": "Are you asking for yourself, or for someone else in your family?",
    "hi": "क्या आप अपने लिए पूछ रहे हैं, या परिवार के किसी अन्य सदस्य के लिए?",
}

# Maps natural English/Bengali/Banglish/Hindi relationship phrases -> (canonical_role, broad_relation, default_sex, default_name)
_RELATION_PATTERNS: tuple[tuple[re.Pattern[str], str, str, str | None, str], ...] = (
    (re.compile(r"(?i)\b(?:my\s+father|father|dad|amar\s+baba(?:r|ke)?|baba(?:r)?|mere\s+papa|papa|pita)\b|আমার\s*বাবা(?:র|কে)?|বাবা(?:র|কে)?", re.U), "father", "parent", "male", "Father"),
    (re.compile(r"(?i)\b(?:my\s+mother|mother|mom|mum|amar\s+ma(?:yer|r|ke)?|mayer|ma|maa|meri\s+mummy|mummy|mata)\b|(?<!\S)(?:আমার\s*মা(?:য়ের|য়ের|র|কে)?|মা(?:য়ের|য়ের))(?=\s|$|[,.;:!?।])", re.U), "mother", "parent", "female", "Mother"),
    (re.compile(r"(?i)\b(?:my\s+grandfather|grandfather|grandpa|amar\s+dadu(?:r)?|dadu|thakurda|dada\s*ji)\b|আমার\s*(?:দাদু|ঠাকুরদা)(?:র)?", re.U), "grandfather", "grandparent", "male", "Grandfather"),
    (re.compile(r"(?i)\b(?:my\s+grandmother|grandmother|grandma|amar\s+dida(?:r)?|dida|thakuma|dadi|nani)\b|আমার\s*(?:দিদা|ঠাকুমা)(?:র)?", re.U), "grandmother", "grandparent", "female", "Grandmother"),
    (re.compile(r"(?i)\b(?:elder\s+brother|older\s+brother|amar\s+dada(?:r)?|dada(?:r)?|bade\s+bhai)\b|আমার\s*দাদা(?:র)?|বড়\s*ভাই", re.U), "elder_brother", "sibling", "male", "Elder Brother"),
    (re.compile(r"(?i)\b(?:younger\s+brother|brother|amar\s+bhai(?:yer|r)?|bhai|chhoto\s+bhai)\b|আমার\s*ভাই(?:য়ের|য়ের|র)?", re.U), "brother", "sibling", "male", "Brother"),
    (re.compile(r"(?i)\b(?:elder\s+sister|younger\s+sister|sister|amar\s+(?:didi|bon)(?:er|r)?|didi|bon|behen)\b|আমার\s*(?:দিদি|বোন)(?:ের|র)?", re.U), "sister", "sibling", "female", "Sister"),
    (re.compile(r"(?i)\b(?:my\s+husband|husband|amar\s+swami|swami|pati)\b|আমার\s*স্বামী", re.U), "husband", "spouse", "male", "Husband"),
    (re.compile(r"(?i)\b(?:my\s+wife|wife|amar\s+bou|bou|stri|patni)\b|আমার\s*স্ত্রী", re.U), "wife", "spouse", "female", "Wife"),
    (re.compile(r"(?i)\b(?:my\s+son|son|amar\s+chele(?:r)?|chele|beta)\b|আমার\s*ছেলে(?:র)?", re.U), "son", "child", "male", "Son"),
    (re.compile(r"(?i)\b(?:my\s+daughter|daughter|amar\s+meye(?:r)?|meye|beti)\b|আমার\s*মেয়ে(?:র)?", re.U), "daughter", "child", "female", "Daughter"),
)

_AGE_IN_TEXT = re.compile(
    r"(?i)(?:age\s*(?:is)?\s*|aged\s+|বয়স\s*|বয়স\s*|umar\s*)?(\d{1,3})\s*(?:years?\s*old|yrs?|years?|বছর|বৎসর|bochor|saal)\b"
)
_CHRONIC_CONDITIONS = (
    ("Diabetes", re.compile(r"(?i)\b(?:diabetes|diabetic|sugar|ডায়াবেটিস|ডায়াবেটিস|সুগার|मधुमेह|शुगर)\b", re.U)),
    ("Hypertension", re.compile(r"(?i)\b(?:hypertension|high\s+bp|high\s+blood\s+pressure|প্রেসার|উচ্চ\s*রক্তচাপ|ब्लड\s*प्रेशर)\b", re.U)),
    ("Thyroid disorder", re.compile(r"(?i)\b(?:thyroid|hypothyroid|hyperthyroid|থাইরয়েড|थायरॉइड)\b", re.U)),
    ("Asthma", re.compile(r"(?i)\b(?:asthma|হাঁপানি|दमा)\b", re.U)),
    ("Kidney disease", re.compile(r"(?i)\b(?:kidney\s+disease|ckd|renal|কিডনি)\b", re.U)),
    ("Heart disease", re.compile(r"(?i)\b(?:heart\s+disease|cardiac|হার্টের\s*সমস্যা)\b", re.U)),
)
_MED_LIST = re.compile(
    r"(?i)\b(metformin|glimepiride|insulin|telmisartan|telma|amlodipine|atorvastatin|"
    r"ecosprin|aspirin|thyronorm|paracetamol|dolo|pantoprazole|losartan|ramipril)\b"
)
_ALLERGY_LIST = re.compile(r"(?i)(?:allergic\s+to|allergy\s+to)\s+([a-zA-Z0-9\s\-]{2,35})|([a-zA-Z0-9\s\-]{2,25})\s+(?:অ্যালার্জি|এলার্জি)")


@dataclass
class RelationMatch:
    canonical_role: str
    broad_relation: str
    default_sex: str | None
    default_name: str


@dataclass
class FamilyResolutionResult:
    matched: bool
    member_id: str | None = None
    member_name: str | None = None
    relation: str | None = None
    canonical_role: str | None = None
    ambiguous: bool = False
    candidates: list[dict[str, Any]] = field(default_factory=list)
    clarification_question: str | None = None
    auto_created: bool = False


def detect_relationship_mention(text: str) -> RelationMatch | None:
    """Detect a family relationship mention in English, Bengali, Banglish, or Hindi."""
    t = (text or "").strip()
    if not t:
        return None
    for pat, role, broad, sex, def_name in _RELATION_PATTERNS:
        if pat.search(t):
            return RelationMatch(
                canonical_role=role,
                broad_relation=broad,
                default_sex=sex,
                default_name=def_name,
            )
    return None


def _member_matches_role(member_name: str, member_relation: str, member_sex: str | None, role: RelationMatch) -> bool:
    name_l = (member_name or "").strip().lower()
    if role.canonical_role.replace("_", " ") in name_l or role.default_name.lower() == name_l:
        return True
    if member_relation != role.broad_relation:
        return False
    if role.default_sex and member_sex and member_sex != role.default_sex:
        return False
    return True


async def resolve_family_member(
    db: AsyncSession,
    cu: CurrentUser,
    text: str,
    *,
    lang: str = "bn",
    auto_create_if_missing: bool = False,
) -> FamilyResolutionResult:
    """Resolve a relationship or member name in `text` to a specific HouseholdMember."""
    aid, acipher = cu.account_id, cu.account_cipher
    links = list(await db.scalars(
        select(HouseholdMember).where(HouseholdMember.user_id == aid).order_by(HouseholdMember.created_at)
    ))

    # Load member metadata securely
    members_meta: list[dict[str, Any]] = []
    for link in links:
        name = acipher.dec(link.name_enc)
        await scope_to(db, link.member_id, aid)
        p = await db.get(PatientProfile, link.member_id)
        members_meta.append({
            "id": link.member_id,
            "name": name,
            "relation": link.relation,
            "sex": p.sex if p else None,
        })
    await scope_to(db, cu.id, aid)

    # 1. Exact member name mention
    t_low = (text or "").lower()
    for m in members_meta:
        mname = (m["name"] or "").strip().lower()
        if mname and len(mname) >= 2 and re.search(rf"\b{re.escape(mname)}\b", t_low):
            return FamilyResolutionResult(
                matched=True,
                member_id=m["id"],
                member_name=m["name"],
                relation=m["relation"],
                canonical_role=m["relation"],
            )

    # 2. Relationship phrase resolution
    role = detect_relationship_mention(text)
    if role is None:
        return FamilyResolutionResult(matched=False)

    hits = [m for m in members_meta if _member_matches_role(m["name"], m["relation"], m["sex"], role)]
    if len(hits) == 1:
        h = hits[0]
        return FamilyResolutionResult(
            matched=True,
            member_id=h["id"],
            member_name=h["name"],
            relation=h["relation"],
            canonical_role=role.canonical_role,
        )
    if len(hits) > 1:
        names = ", ".join(h["name"] for h in hits)
        q = (
            f"আপনার পরিবারে একাধিক সদস্য ({names}) রয়েছেন। আপনি কার কথা বলছেন?"
            if lang == "bn"
            else f"Which family member are you referring to ({names})?"
        )
        return FamilyResolutionResult(
            matched=False,
            ambiguous=True,
            candidates=hits,
            clarification_question=q,
            canonical_role=role.canonical_role,
        )

    # 3. Auto-create member profile if requested & within limit
    if auto_create_if_missing and len(members_meta) < MAX_FAMILY_MEMBERS:
        account = cu.account or cu.user
        member = User(dek_wrapped=new_wrapped_dek(), preferred_lang=account.preferred_lang, is_guest=False)
        db.add(member)
        await db.flush()
        db.add(HouseholdMember(
            user_id=aid,
            member_id=member.id,
            name_enc=acipher.enc(role.default_name),
            relation=role.broad_relation,
        ))
        await scope_to(db, member.id, aid)
        db.add(PatientProfile(user_id=member.id, sex=role.default_sex))
        import json as _json
        db.add(AuditEvent(
            user_id=member.id,
            actor_id=aid,
            actor_role="patient",
            action="family_member_auto_created",
            entity_type="household_member",
            entity_id=member.id,
            detail_enc=Cipher(member.dek_wrapped, member.id).enc(_json.dumps({"role": role.canonical_role})),
        ))
        await db.commit()
        await scope_to(db, cu.id, aid)
        return FamilyResolutionResult(
            matched=True,
            member_id=member.id,
            member_name=role.default_name,
            relation=role.broad_relation,
            canonical_role=role.canonical_role,
            auto_created=True,
        )

    return FamilyResolutionResult(matched=False, canonical_role=role.canonical_role)


async def get_member_current_user(db: AsyncSession, cu: CurrentUser, member_id: str) -> CurrentUser | None:
    """Return a CurrentUser scoped to `member_id` after verifying account ownership."""
    link = (await db.scalars(
        select(HouseholdMember).where(HouseholdMember.user_id == cu.account_id, HouseholdMember.member_id == member_id)
    )).first()
    if link is None:
        return None
    await scope_to(db, member_id, cu.account_id)
    member = await db.get(User, member_id)
    if member is None:
        return None
    return CurrentUser(
        user=member,
        account=cu.account or cu.user,
        cipher=Cipher(member.dek_wrapped, member.id),
    )


async def update_member_health_from_text(
    db: AsyncSession,
    member_cu: CurrentUser,
    text: str,
    *,
    source: str = "chat",
) -> dict[str, Any]:
    """Extract and persist age, conditions, medications, allergies, and vitals for a specific family member.

    Never overwrites historical vitals — always appends a new timestamped Vital row.
    """
    t = (text or "").strip()
    if not t:
        return {"updated": []}

    await scope_to(db, member_cu.id, member_cu.account_id)
    updated: list[str] = []
    now = local_now().replace(tzinfo=None)

    # 1. Age -> PatientProfile.dob_enc (only if not already set)
    m_age = _AGE_IN_TEXT.search(t)
    if m_age:
        age_val = int(m_age.group(1))
        if 0 <= age_val <= 120:
            prof = await db.get(PatientProfile, member_cu.id)
            if prof is None:
                prof = PatientProfile(user_id=member_cu.id)
                db.add(prof)
            if not prof.dob_enc:
                est_dob = date(max(1900, now.year - age_val), 1, 1)
                prof.dob_enc = member_cu.cipher.enc(est_dob.isoformat())
                updated.append(f"age:{age_val}")

    # 2. Chronic Conditions (deduplicated)
    existing_conds = {
        member_cu.cipher.dec(c.display_enc).lower()
        for c in await db.scalars(select(Condition).where(Condition.user_id == member_cu.id))
    }
    for cname, pat in _CHRONIC_CONDITIONS:
        if pat.search(t) and cname.lower() not in existing_conds:
            db.add(Condition(
                user_id=member_cu.id,
                display_enc=member_cu.cipher.enc(cname),
                status="active",
                source=source,
            ))
            existing_conds.add(cname.lower())
            updated.append(f"condition:{cname}")

    # 3. Medications (deduplicated)
    existing_meds = {
        member_cu.cipher.dec(m.name_enc).lower()
        for m in await db.scalars(select(Medication).where(Medication.user_id == member_cu.id))
    }
    for mm in _MED_LIST.finditer(t):
        med_name = mm.group(1).strip().title()
        if med_name.lower() not in existing_meds:
            db.add(Medication(
                user_id=member_cu.id,
                name_enc=member_cu.cipher.enc(med_name),
                status="active",
            ))
            existing_meds.add(med_name.lower())
            updated.append(f"medication:{med_name}")

    # 4. Allergies (deduplicated)
    existing_al = {
        member_cu.cipher.dec(a.substance_enc).lower()
        for a in await db.scalars(select(Allergy).where(Allergy.user_id == member_cu.id))
    }
    for am in _ALLERGY_LIST.finditer(t):
        sub = (am.group(1) or am.group(2) or "").strip()
        if sub and sub.lower() not in existing_al:
            db.add(Allergy(
                user_id=member_cu.id,
                substance_enc=member_cu.cipher.enc(sub),
                type="drug",
                source=source,
            ))
            existing_al.add(sub.lower())
            updated.append(f"allergy:{sub}")

    # 5. Vitals (BP, Sugar, HR, Temp, SpO2, Weight) — ALWAYS append new reading, preserving history
    is_last_week = bool(re.search(r"(?i)last\s+week|গত\s*সপ্তাহ|पिछले\s*सप्ताह", t))
    measured_at = now
    if is_last_week:
        from datetime import timedelta
        measured_at = now - timedelta(days=7)

    parsed_vitals = parse_vitals(t)
    units = {"bp_sys": "mmHg", "bp_dia": "mmHg", "glucose_f": "mg/dL", "glucose_pp": "mg/dL",
             "glucose_r": "mg/dL", "weight": "kg", "hr": "bpm", "spo2": "%", "temp": "°C"}
    for pv in parsed_vitals:
        vtype = pv["type"]
        db.add(Vital(
            user_id=member_cu.id,
            type=vtype,
            value=float(pv["value"]),
            unit=units.get(vtype, ""),
            measured_at=measured_at,
            source=source,
            is_estimate=False,
            original_statement_enc=member_cu.cipher.enc(t[:300]),
        ))
        updated.append(f"vital:{vtype}={pv['value']}")

    if updated:
        import json as _json
        db.add(AuditEvent(
            user_id=member_cu.id,
            actor_id=member_cu.account_id,
            actor_role="patient",
            action="family_health_memory_updated",
            entity_type="patient_profile",
            entity_id=member_cu.id,
            detail_enc=member_cu.cipher.enc(_json.dumps({"updated": updated})),
        ))
        await db.commit()

    return {"updated": updated}


async def get_member_memory_summary(
    db: AsyncSession,
    member_cu: CurrentUser,
    *,
    name: str = "",
    relation: str = "",
) -> dict[str, Any]:
    """Retrieve ONLY the selected family member's isolated profile, conditions, meds, allergies, and vital history."""
    await scope_to(db, member_cu.id, member_cu.account_id)
    prof = await db.get(PatientProfile, member_cu.id)
    age = None
    if prof and prof.dob_enc:
        try:
            dob = date.fromisoformat(member_cu.cipher.dec(prof.dob_enc))
            age = int((datetime.now(timezone.utc).date() - dob).days // 365.25)
        except ValueError:
            age = None

    conds = [member_cu.cipher.dec(c.display_enc) for c in await db.scalars(
        select(Condition).where(Condition.user_id == member_cu.id, Condition.status == "active")
    )]
    meds = [member_cu.cipher.dec(m.name_enc) for m in await db.scalars(
        select(Medication).where(Medication.user_id == member_cu.id, Medication.status == "active")
    )]
    allergies = [member_cu.cipher.dec(a.substance_enc) for a in await db.scalars(
        select(Allergy).where(Allergy.user_id == member_cu.id)
    )]

    # Latest + previous readings per vital type (for comparison without overwriting history)
    vital_rows = list(await db.scalars(
        select(Vital).where(Vital.user_id == member_cu.id).order_by(Vital.measured_at.desc())
    ))
    vitals_by_type: dict[str, list[dict[str, Any]]] = {}
    for v in vital_rows:
        vitals_by_type.setdefault(v.type, []).append({
            "value": v.value,
            "unit": v.unit,
            "measured_at": v.measured_at.isoformat() if v.measured_at else None,
            "source": v.source,
        })

    return {
        "member_id": member_cu.id,
        "name": name,
        "relation": relation,
        "age": age,
        "sex": prof.sex if prof else None,
        "conditions": conds,
        "medications": meds,
        "allergies": allergies,
        "vitals_history": vitals_by_type,
        "latest_bp": (
            f"{int(vitals_by_type['bp_sys'][0]['value'])}/{int(vitals_by_type['bp_dia'][0]['value'])}"
            if "bp_sys" in vitals_by_type and "bp_dia" in vitals_by_type else None
        ),
        "known_fields": [
            k for k, present in (
                ("age", age is not None),
                ("conditions", bool(conds)),
                ("medications", bool(meds)),
                ("allergies", bool(allergies)),
                ("bp", "bp_sys" in vitals_by_type),
                ("blood_glucose", any(k.startswith("glucose_") for k in vitals_by_type)),
            ) if present
        ],
    }
