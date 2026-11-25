"""PatientProfileService: Greeting, conversational name/age onboarding, consent-safe updates & minor care."""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Allergy, Condition, Encounter, HouseholdMember, Medication, PatientProfile
from app.security.auth import CurrentUser
from app.security.consent import active_purposes

_BN_HI_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯०१२३४५६७८९", "01234567890123456789")

GREETINGS: dict[str, str] = {
    "bn": "আমি Sushrut AI, কীভাবে আপনাকে সাহায্য করতে পারি?",
    "en": "Hello, I am SUSHRUT AI. How can I help you today?",
    "hi": "नमस्ते, मैं SUSHRUT AI हूँ। मैं आपकी कैसे मदद कर सकता हूँ?",
}

FOLLOWUP_READY_PROMPT: dict[str, str] = {
    "bn": "আপনাকে কী সমস্যার জন্য সাহায্য করতে পারি?",
    "en": "What health concern can I help you with today?",
    "hi": "मैं आपकी किस स्वास्थ्य समस्या में मदद कर सकता हूँ?",
}

_NAME_ASK: dict[str, str] = {
    "bn": "শুরু করার আগে আপনার নাম কী জানতে পারি?",
    "en": "Before we begin, what is your name?",
    "hi": "शुरू करने से पहले, आपका नाम क्या है?",
}

_AGE_ASK: dict[str, str] = {
    "bn": "আপনার বয়স কত?",
    "en": "How old are you?",
    "hi": "आपकी उम्र कितनी है?",
}

_GENDER_ASK: dict[str, str] = {
    "bn": "আপনার লিঙ্গ কী? (পুরুষ, মহিলা, অন্যান্য, অথবা বলতে চাই না)",
    "en": "What is your gender? (Male, Female, Other, or Prefer not to say)",
    "hi": "आपका लिंग क्या है? (पुरुष, महिला, अन्य, या बताना नहीं चाहते)",
}

_NAME_AGE_ASK: dict[str, str] = {
    "bn": "শুরু করার আগে আপনার নাম কী জানতে পারি?",
    "en": "Before we begin, what is your name?",
    "hi": "शुरू करने से पहले, आपका नाम क्या है?",
}

# Session-scoped ephemeral identity store when storage consent is disabled (or before commit).
_EPHEMERAL_LOCK = threading.Lock()
_EPHEMERAL_IDENTITY: dict[str, dict[str, Any]] = {}


def _age_from_dob(dob_raw: str | None) -> int | None:
    if not dob_raw:
        return None
    try:
        d = date.fromisoformat(dob_raw[:10])
    except ValueError:
        return None
    t = date.today()
    return max(0, t.year - d.year - ((t.month, t.day) < (d.month, d.day)))


_NAME_PATTERNS = (
    re.compile(r"(?i)(?:my\s+name\s+is|i\s+am\s+called|call\s+me|name\s*[:\-]\s*|correct\s+my\s+name\s+to|change\s+my\s+name\s+to)\s+([A-Za-z\u0980-\u09FF\u0900-\u097F][A-Za-z\u0980-\u09FF\u0900-\u097F .'-]{1,40})"),
    re.compile(r"(?:আমার\s*নাম|নাম(?:\s*হল|\s*হলো)?)\s*[:\-]?\s*([\u0980-\u09FFA-Za-z][\u0980-\u09FFA-Za-z .'-]{1,40})"),
    re.compile(r"(?i)\b(?:amar\s+naam|naam)\s+([A-Za-z][A-Za-z .'-]{1,40})"),
    re.compile(r"(?:मेरा\s*नाम|नाम)\s*[:\-]?\s*([\u0900-\u097FA-Za-z][\u0900-\u097FA-Za-z .'-]{1,40})"),
)

_AGE_PATTERNS = (
    re.compile(r"(?i)(?:my\s+age\s+is|i\s+am|i'm|age(?:\s+is|\s+to)?|correct\s+my\s+age\s+to|change\s+my\s+age\s+to)\s+(\d{1,3})\s*(?:years?|yrs?|yo|বছর|साल)?\b"),
    re.compile(r"(?i)\b(\d{1,3})\s*(?:years?\s*old|yrs?\s*old|বছর\s*বয়স|বছর\s*বয়স|साल\s*का|साल\s*की)\b"),
    re.compile(r"(?:বয়স|বয়স|boyosh|umar|उम्र)\s*(?:হল|হলো|হবে|is|:|-)?\s*(\d{1,3})\b", re.IGNORECASE),
)

_NAME_STOP_TAIL = re.compile(
    r"(?i)\s+(?:and|my|age|i\s+am|i'm|years?|yrs?|এবং|আর|আমার|বয়স|বয়স|boyosh|और|मेरी|उम्र)\b.*$"
)
_SYMPTOM_SIGNAL = re.compile(
    r"(?i)(?:pain|fever|cough|headache|throat|sore|cold|dizzy|dizziness|vomit|nausea|chest|breath|bleeding|rash|acidity|diarrhea|diarrhoea|fatigue|weakness|"
    r"ব্যথা|জ্বর|কাশি|গলা|সর্দি|মাথা|বমি|শ্বাস|বুকে|পেটে|পায়খানা|পায়খানা|অসুস্থ|দুর্বল|दर्द|बुखार|खांसी|गला|सिर|उल्टी|सांस|पेट)"
)
_CORRECTION_SIGNAL = re.compile(r"(?i)(?:correct|update|change|actually|wrong|not\s+\d+|ভুল|সঠিক|পরিবর্তন|বয়স.*হবে|বয়স.*হবে|गलत|सही)")
_DECLINE_SIGNAL = re.compile(
    r"(?i)(?:prefer\s+not\s+to\s+say|don'?t\s+(?:know|remember|want\s+to\s+say)|not\s+sure|forgot|skip|unavailable|"
    r"মনে\s*নেই|জানি\s*না|বলতে\s*চাই\s*না|জানিনা|মনে\s*পড়ছে\s*না|মনে\s*পড়ছে\s*না|স্কিপ|"
    r"याद\s*नहीं|नहीं\s*पता|बताना\s*नहीं)"
)


@dataclass
class ExtractedIdentity:
    name: str | None = None
    age: int | None = None
    gender: str | None = None
    location: str | None = None
    declined_field: str | None = None
    is_correction: bool = False


@dataclass
class PatientSnapshot:
    user_id: str
    name: str | None = None
    age: int | None = None
    sex: str | None = None
    location: str | None = None
    pregnancy_status: str | None = None
    allergies: list[str] = None  # type: ignore[assignment]
    conditions: list[str] = None  # type: ignore[assignment]
    medications: list[str] = None  # type: ignore[assignment]
    unavailable_fields: list[str] = None  # type: ignore[assignment]
    preferred_lang: str = "en"
    is_returning: bool = False
    first_consultation: bool = True
    minor_patient: bool = False
    can_store: bool = True

    def __post_init__(self) -> None:
        self.allergies = self.allergies or []
        self.conditions = self.conditions or []
        self.medications = self.medications or []
        self.unavailable_fields = self.unavailable_fields or []

    def as_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "name": self.name if self.name else ("unavailable" if "name" in self.unavailable_fields else None),
            "age": self.age if self.age is not None else ("unavailable" if "age" in self.unavailable_fields else None),
            "sex": self.sex if self.sex else ("unavailable" if "gender" in self.unavailable_fields else None),
            "location": self.location,
            "pregnancy_status": self.pregnancy_status,
            "allergies": self.allergies,
            "conditions": self.conditions,
            "medications": self.medications,
            "unavailable_fields": list(self.unavailable_fields),
            "preferred_lang": self.preferred_lang,
            "is_returning": self.is_returning,
            "first_consultation": self.first_consultation,
            "minor_patient": self.minor_patient,
            "can_store": self.can_store,
        }


class PatientProfileService:
    """Handles greeting, consent-aware patient identification, correction, and clinical profile snapshot."""

    def greeting_for(self, lang: str) -> str:
        clean = (lang or "en").split("-")[0].lower()
        return GREETINGS.get(clean, GREETINGS["en"])

    def ready_prompt_for(self, lang: str) -> str:
        clean = (lang or "en").split("-")[0].lower()
        return FOLLOWUP_READY_PROMPT.get(clean, FOLLOWUP_READY_PROMPT["en"])

    def extract_name_and_age(self, text: str, *, pending_field: str | None = None) -> ExtractedIdentity:
        raw = (text or "").strip().translate(_BN_HI_DIGITS)
        if not raw:
            return ExtractedIdentity()
        name: str | None = None
        for pat in _NAME_PATTERNS:
            m = pat.search(raw)
            if m:
                cand = _NAME_STOP_TAIL.sub("", m.group(1)).strip(" ,.;:।!?")
                if cand and len(cand) >= 2 and not _SYMPTOM_SIGNAL.search(cand):
                    name = cand[:80]
                    break
        age: int | None = None
        for pat in _AGE_PATTERNS:
            m = pat.search(raw)
            if m:
                try:
                    val = int(m.group(1))
                    if 0 <= val <= 125:
                        age = val
                        break
                except ValueError:
                    pass
        gender: str | None = None
        if re.search(r"(?i)\b(prefer not to say|don'?t want to say|skip gender)\b|বলতে চাই না|बताना नहीं", raw):
            gender = "prefer_not_to_say"
        elif re.search(r"(?i)\b(female|woman|girl)\b|মহিলা|নারী|মেয়ে|মেয়ে|महिला|औरत", raw):
            gender = "female"
        elif re.search(r"(?i)\b(male|man|boy)\b|পুরুষ|ছেলে|पुरुष|लड़का", raw):
            gender = "male"
        elif re.search(r"(?i)\b(non[- ]?binary|other gender|transgender)\b|অন্যান্য|अन्य", raw):
            gender = "other"

        declined_field: str | None = None
        if pending_field == "age" and age is None:
            m_bare_age = re.fullmatch(r"\s*(\d{1,3})\s*(?:years?|yrs?|yo|বছর|साल)?\s*[।.]?\s*", raw, re.IGNORECASE)
            if m_bare_age:
                val = int(m_bare_age.group(1))
                if 0 <= val <= 125:
                    age = val
        elif pending_field == "name" and name is None and not _SYMPTOM_SIGNAL.search(raw) and not _DECLINE_SIGNAL.search(raw):
            cand = raw.strip(" ,.;:।!?")
            if 2 <= len(cand) <= 45 and len(cand.split()) <= 4 and not re.search(r"\d", cand):
                name = cand

        if _DECLINE_SIGNAL.search(raw) and pending_field in ("name", "age", "gender"):
            if (pending_field == "name" and not name) or (pending_field == "age" and age is None) or (pending_field == "gender" and not gender):
                declined_field = pending_field

        return ExtractedIdentity(
            name=name,
            age=age,
            gender=gender,
            declined_field=declined_field,
            is_correction=bool(_CORRECTION_SIGNAL.search(raw)),
        )

    async def load_snapshot(self, db: AsyncSession, cu: CurrentUser, *, session_id: str | None = None) -> PatientSnapshot:
        purposes = await active_purposes(db, cu.account_id)
        can_store = "core_service" in purposes
        p = await db.get(PatientProfile, cu.id)

        name = cu.cipher.dec(p.name_enc) if (p and p.name_enc) else None
        if cu.is_member and not name:
            link = (await db.scalars(
                select(HouseholdMember).where(HouseholdMember.user_id == cu.account_id, HouseholdMember.member_id == cu.id)
            )).first()
            name = cu.account_cipher.dec(link.name_enc) if link else None

        dob = cu.cipher.dec(p.dob_enc) if (p and p.dob_enc) else None
        age = _age_from_dob(dob)
        if age is None and p and p.age_years is not None:
            age = int(p.age_years)
        gender = cu.cipher.dec(p.gender_enc) if (p and p.gender_enc) else (p.sex if p else None)

        # Merge ephemeral session identity if not stored in DB
        unavailable_fields: list[str] = []
        if session_id:
            with _EPHEMERAL_LOCK:
                eph = _EPHEMERAL_IDENTITY.get(session_id) or {}
                if not name and eph.get("name"):
                    name = eph["name"]
                if age is None and eph.get("age") is not None:
                    age = eph["age"]
                if not gender and eph.get("gender"):
                    gender = eph["gender"]
                unavailable_fields = list(eph.get("declined") or [])

        allergies = [cu.cipher.dec(a.substance_enc) for a in await db.scalars(select(Allergy).where(Allergy.user_id == cu.id))]
        conditions = [cu.cipher.dec(c.display_enc) for c in await db.scalars(select(Condition).where(Condition.user_id == cu.id, Condition.status == "active"))]
        meds = [cu.cipher.dec(m.name_enc) for m in await db.scalars(select(Medication).where(Medication.user_id == cu.id, Medication.status == "active"))]
        prior_enc = (await db.scalars(select(Encounter.id).where(Encounter.user_id == cu.id).limit(1))).first()

        is_returning = bool(prior_enc or (name and age is not None) or conditions or meds)
        loc = ", ".join(x for x in [p.district if p else None, p.state_code if p else None] if x) or None
        return PatientSnapshot(
            user_id=cu.id,
            name=name,
            age=age,
            sex=gender,
            location=loc,
            pregnancy_status=p.pregnancy_status if p else None,
            allergies=[x for x in allergies if x],
            conditions=[x for x in conditions if x],
            medications=[x for x in meds if x],
            unavailable_fields=unavailable_fields,
            preferred_lang=cu.user.preferred_lang or "en",
            is_returning=is_returning,
            first_consultation=not is_returning,
            minor_patient=(age is not None and age < 18),
            can_store=can_store,
        )

    async def apply_identity_from_turn(
        self,
        db: AsyncSession,
        cu: CurrentUser,
        text: str,
        *,
        session_id: str | None = None,
        allow_persist: bool = True,
        pending_field: str | None = None,
    ) -> tuple[PatientSnapshot, dict[str, Any]]:
        """Extract name/age (including explicit corrections) from conversational text without inventing data."""
        ext = self.extract_name_and_age(text, pending_field=pending_field)
        snap = await self.load_snapshot(db, cu, session_id=session_id)
        updated_fields: list[str] = []

        if ext.name and (ext.is_correction or not snap.name or ext.name != snap.name):
            snap.name = ext.name
            if "name" in snap.unavailable_fields:
                snap.unavailable_fields.remove("name")
            updated_fields.append("name")
        if ext.age is not None and (ext.is_correction or snap.age is None or ext.age != snap.age):
            snap.age = ext.age
            snap.minor_patient = ext.age < 18
            if "age" in snap.unavailable_fields:
                snap.unavailable_fields.remove("age")
            updated_fields.append("age")
        if ext.gender and (ext.is_correction or not snap.sex or ext.gender != snap.sex):
            snap.sex = ext.gender
            if "gender" in snap.unavailable_fields:
                snap.unavailable_fields.remove("gender")
            updated_fields.append("gender")
        if ext.declined_field and ext.declined_field not in snap.unavailable_fields:
            snap.unavailable_fields.append(ext.declined_field)
            updated_fields.append(f"{ext.declined_field}:unavailable")

        if updated_fields and session_id:
            with _EPHEMERAL_LOCK:
                cur = _EPHEMERAL_IDENTITY.setdefault(session_id, {})
                if snap.name:
                    cur["name"] = snap.name
                if snap.age is not None:
                    cur["age"] = snap.age
                if snap.sex:
                    cur["gender"] = snap.sex
                cur["declined"] = list(snap.unavailable_fields)

        if updated_fields and allow_persist and snap.can_store:
            p = await db.get(PatientProfile, cu.id) or PatientProfile(user_id=cu.id)
            if "name" in updated_fields and snap.name:
                p.name_enc = cu.cipher.enc(snap.name)
                if cu.is_member:
                    link = (await db.scalars(
                        select(HouseholdMember).where(HouseholdMember.user_id == cu.account_id, HouseholdMember.member_id == cu.id)
                    )).first()
                    if link:
                        link.name_enc = cu.account_cipher.enc(snap.name)
            if "age" in updated_fields and snap.age is not None:
                p.age_years = snap.age
                p.dob_enc = None
            if "gender" in updated_fields and snap.sex:
                p.gender_enc = cu.cipher.enc(snap.sex)
                if snap.sex in ("male", "female"):
                    p.sex = snap.sex
            db.add(p)
            await db.commit()

        return snap, {
            "updated": updated_fields,
            "is_correction": ext.is_correction and bool(updated_fields),
            "pure_identity_turn": bool(updated_fields) and not _SYMPTOM_SIGNAL.search(text or ""),
        }

    def missing_profile_fields(self, snap: PatientSnapshot) -> list[str]:
        missing: list[str] = []
        unavail = set(snap.unavailable_fields or [])
        if not snap.name and "name" not in unavail:
            missing.append("name")
        if snap.age is None and "age" not in unavail:
            missing.append("age")
        if not snap.sex and "gender" not in unavail:
            missing.append("gender")
        return missing

    def next_missing_profile_question(self, snap: PatientSnapshot, *, lang: str) -> tuple[str | None, str | None]:
        clean = (lang or "en").split("-")[0].lower()
        missing = self.missing_profile_fields(snap)
        if not missing:
            return None, None
        first_missing = missing[0]
        if first_missing == "name":
            return "name", _NAME_ASK.get(clean, _NAME_ASK["en"])
        if first_missing == "age":
            return "age", _AGE_ASK.get(clean, _AGE_ASK["en"])
        return "gender", _GENDER_ASK.get(clean, _GENDER_ASK["en"])

    def onboarding_prompt(self, snap: PatientSnapshot, *, lang: str) -> dict[str, Any]:
        """Build the initial greeting + identity confirmation or missing-field inquiry."""
        clean = (lang or "en").split("-")[0].lower()
        greeting = self.greeting_for(clean)
        missing = self.missing_profile_fields(snap)

        if not missing:
            if clean == "bn":
                confirm = f"স্বাগতম, {snap.name} (বয়স {snap.age} বছর)। এই তথ্য কি ঠিক আছে? {self.ready_prompt_for(clean)}"
            elif clean == "hi":
                confirm = f"स्वागत है, {snap.name} (उम्र {snap.age} वर्ष)। क्या यह जानकारी सही है? {self.ready_prompt_for(clean)}"
            else:
                confirm = f"Welcome back, {snap.name} (age {snap.age}). Is that still correct? {self.ready_prompt_for(clean)}"
            return {
                "greeting": greeting,
                "prompt": f"{greeting} {confirm}",
                "missing_fields": [],
                "status": "confirm_returning",
                "patient": snap.as_dict(),
            }

        # Ask one onboarding question at a time: Name -> Age -> Gender
        first_missing = missing[0]
        if first_missing == "name":
            ask = _NAME_ASK.get(clean, _NAME_ASK["en"])
        elif first_missing == "age":
            ask = _AGE_ASK.get(clean, _AGE_ASK["en"])
        else:
            ask = _GENDER_ASK.get(clean, _GENDER_ASK["en"])

        return {
            "greeting": greeting,
            "prompt": f"{greeting} {ask}",
            "missing_fields": missing,
            "status": "collect_missing",
            "patient": snap.as_dict(),
        }

    def minor_guidance_note(self, snap: PatientSnapshot, *, lang: str) -> str | None:
        if not snap.minor_patient:
            return None
        clean = (lang or "en").split("-")[0].lower()
        if clean == "bn":
            return "রোগী অপ্রাপ্তবয়স্ক (১৮ বছরের কম)। অনুগ্রহ করে যেকোনো সিদ্ধান্ত ও চিকিৎসকের পরামর্শের সময় অভিভাবককে সঙ্গে রাখুন।"
        if clean == "hi":
            return "रोगी की उम्र 18 वर्ष से कम है। कृपया डॉक्टर से परामर्श और देखभाल के दौरान माता-पिता या अभिभावक को साथ रखें।"
        return "Because the patient is under 18, please involve a parent or guardian and consult a pediatrician or doctor."
