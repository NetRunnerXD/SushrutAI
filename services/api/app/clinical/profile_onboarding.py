"""Seamless first-interaction profile onboarding (name + age) for family/relation differentiation."""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PatientProfile
from app.security.auth import CurrentUser

_DIGIT_MAP = str.maketrans("০১২৩৪৫৬৭৮৯०१२३४५६७८९", "01234567890123456789")

_THIRD_PARTY_GUARD = re.compile(
    r"(?i)\b(?:my\s+(?:mother|mom|mama|father|dad|papa|brother|sister|wife|husband|son|daughter|child|baby|uncle|aunt|friend|grandmother|grandfather)|"
    r"amar\s+(?:ma|maa|baba|bhai|bon|dada|didi)|"
    r"আমার\s+(?:মা|বাবা|ভাই|বোন|দাদা|দিদি|স্ত্রী|স্বামী|ছেলে|মেয়ে)|"
    r"मेरी\s+(?:माँ|मां|बहन|पत्नी|बेटी)|मेरे\s+(?:पिता|पापा|भाई|पति|बेटे))\b"
)

_SYMPTOM_GUARD = re.compile(
    r"(?i)\b(?:fever|pain|ache|cough|cold|dizzy|headache|vomit|nausea|chest|breath|sugar|bp|report|medicine|tablet|"
    r"জ্বর|ব্যথা|কাশি|মাথা|বমি|ওষুধ|রিপোর্ট|বুखार|दर्द|खांसी|सिरदर्द|दवा)\b"
)

_NAME_PATTERNS = (
    re.compile(r"(?i)(?:my\s+name\s+is|i\s+am|i'm|this\s+is|call\s+me)\s+([A-Za-z\u0980-\u09FF\u0900-\u097F][A-Za-z\u0980-\u09FF\u0900-\u097F .'-]{1,38})"),
    re.compile(r"(?:আমার\s+নাম|আমি)\s+([\u0980-\u09FFa-zA-Z][ \u0980-\u09FFa-zA-Z.'-]{1,36}?)(?:\s*(?:এবং|আর|বয়স|,|$))"),
    re.compile(r"(?:मेरा\s+नाम|मैं)\s+([\u0900-\u097Fa-zA-Z][ \u0900-\u097Fa-zA-Z.'-]{1,36}?)(?:\s*(?:है|हूँ|और|उम्र|,|$))"),
)

_AGE_PATTERNS = (
    re.compile(r"(?i)\b(?:age\s*(?:is)?|i\s+am|i'm|years?\s*old|yr\s*old)\s*[:=]?\s*(\d{1,3})\b"),
    re.compile(r"(?i)\b(\d{1,3})\s*(?:years?\s*old|yrs?\s*old|yrs?|y/o)\b"),
    re.compile(r"(?:বয়স|বয়েস)\s*[:=]?\s*(\d{1,3})\b"),
    re.compile(r"(\d{1,3})\s*(?:বছর|साल|वर्ष)"),
    re.compile(r"(?:उम्र)\s*[:=]?\s*(\d{1,3})\b"),
)

_STOP_NAMES = frozenset({
    "fine", "good", "okay", "ok", "well", "sick", "ill", "tired", "here", "back",
    "হ্যালো", "ভালো", "ঠিক", "আমি", "ठीक", "अच्छा", "सही",
})


async def profile_needs_onboarding(db: AsyncSession, cu: CurrentUser) -> tuple[bool, bool, bool, str | None]:
    """Return (missing_name, missing_age, missing_gender, existing_name) for the current user."""
    p = await db.get(PatientProfile, cu.id)
    name = ""
    if p and getattr(p, "name_enc", None):
        try:
            name = (cu.cipher.dec(p.name_enc) or "").strip()
        except Exception:
            name = ""
    dob = ""
    if p and p.dob_enc:
        try:
            dob = (cu.cipher.dec(p.dob_enc) or "").strip()
        except Exception:
            dob = ""
    gender = (getattr(p, "sex", None) or "").strip() if p else ""
    if not gender and p and getattr(p, "gender_enc", None):
        try:
            gender = (cu.cipher.dec(p.gender_enc) or "").strip()
        except Exception:
            gender = ""
    return (not bool(name), not bool(dob), not bool(gender), name or None)


def greeting_with_onboarding(
    lang: str,
    *,
    missing_name: bool,
    missing_age: bool,
    missing_gender: bool = False,
    existing_name: str | None = None,
) -> str:
    """Return a warm, doctor-like SushrutAI greeting that seamlessly asks for missing name, age, and gender."""
    if missing_name or missing_age or missing_gender:
        if lang == "bn":
            items = [x for cond, x in ((missing_name, "নাম"), (missing_age, "বয়স"), (missing_gender, "লিঙ্গ")) if cond]
            asked = ", ".join(items[:-1]) + (" এবং " if len(items) > 1 else "") + items[-1]
            return (
                f"নমস্কার, আমি SushrutAI, আপনার ব্যক্তিগত স্বাস্থ্য সহায়ক। শুরু করার আগে, আপনার {asked} একটু বলবেন? "
                "তাহলে আপনার এবং পরিবারের সবার স্বাস্থ্যতথ্য আলাদাভাবে গুছিয়ে রাখতে পারব।"
            )
        if lang == "hi":
            items = [x for cond, x in ((missing_name, "नाम"), (missing_age, "उम्र"), (missing_gender, "जेंडर")) if cond]
            asked = ", ".join(items[:-1]) + (" और " if len(items) > 1 else "") + items[-1]
            return (
                f"नमस्ते, मैं SushrutAI हूँ, आपका व्यक्तिगत स्वास्थ्य साथी। शुरू करने से पहले, क्या आप अपना {asked} बता सकते हैं? "
                "इससे मैं आपकी और आपके परिवार के सदस्यों की जानकारी अलग-अलग और सही रख पाऊँगा।"
            )
        items = [x for cond, x in ((missing_name, "name"), (missing_age, "age"), (missing_gender, "gender")) if cond]
        asked = ", ".join(items[:-1]) + (", and " if len(items) > 1 else "") + items[-1]
        return (
            f"Hello, I'm SushrutAI, your personal health companion. Before we begin, may I know your {asked}? "
            "That helps me personalize your care and keep your family members' health notes clearly separated."
        )

    if lang == "bn":
        return (
            "নমস্কার, আমি SushrutAI, আপনার ব্যক্তিগত স্বাস্থ্য সহায়ক। "
            "তাড়াহুড়ো নেই, আরাম করে বলুন — আজ আপনার শরীর কেমন লাগছে, বা পরিবারের কারও জন্য কিছু জানতে চাইছেন?"
        )
    if lang == "hi":
        return (
            "नमस्ते, मैं SushrutAI हूँ, आपका व्यक्तिगत स्वास्थ्य साथी। "
            "आराम से बताइए — आज आपकी तबीयत कैसी है, या परिवार के किसी सदस्य के बारे में पूछना चाहते हैं?"
        )
    return (
        "Hello, I'm SushrutAI, your personal health companion. "
        "Please take your time and tell me — how are you feeling today, or are you asking for yourself or a family member?"
    )


def _extract_name_age_gender(text: str) -> tuple[str | None, int | None, str | None]:
    from app.clinical.subject_context import parse_gender

    norm = (text or "").translate(_DIGIT_MAP).strip()
    if not norm or _THIRD_PARTY_GUARD.search(norm):
        return None, None, None

    name: str | None = None
    for pat in _NAME_PATTERNS:
        m = pat.search(norm)
        if m:
            cand = re.split(r"[,.;!?]|\b(?:and|age|am|i'm|years?|yrs?|old|male|female)\b", m.group(1), maxsplit=1, flags=re.I)[0].strip()
            words = [w for w in cand.split() if w]
            if 1 <= len(words) <= 3 and cand.lower() not in _STOP_NAMES and not any(c.isdigit() for c in cand):
                name = " ".join(w.capitalize() if w.isascii() else w for w in words)
                break

    age: int | None = None
    for pat in _AGE_PATTERNS:
        m = pat.search(norm)
        if m:
            try:
                v = int(m.group(1))
                if 1 <= v <= 115:
                    age = v
                    break
            except ValueError:
                pass

    gender = parse_gender(norm)

    # Handle compact "Ananya, 34, female" or "Ananya, 34" when no symptoms are mentioned
    if name is None and age is None and not _SYMPTOM_GUARD.search(norm):
        m_compact = re.match(r"^([A-Za-z\u0980-\u09FF\u0900-\u097F]{2,25})(?:\s*,\s*|\s+)(\d{1,3})(?:\b|[,\s])", norm)
        if m_compact:
            cand, age_s = m_compact.group(1).strip(), m_compact.group(2)
            v = int(age_s)
            if cand.lower() not in _STOP_NAMES and 1 <= v <= 115:
                name = cand.capitalize() if cand.isascii() else cand
                age = v

    return name, age, gender


async def capture_onboarding_identity(
    db: AsyncSession,
    cu: CurrentUser,
    text: str,
    *,
    lang: str = "en",
) -> dict[str, Any] | None:
    """Capture user's own name, age, and gender from a natural introduction and save to PatientProfile."""
    name, age, gender = _extract_name_age_gender(text)
    if not name and age is None and not gender:
        return None

    p = await db.get(PatientProfile, cu.id)
    if p is None:
        p = PatientProfile(user_id=cu.id)
        db.add(p)

    updated = False
    if name:
        p.name_enc = cu.cipher.enc(name)
        updated = True
    if age is not None:
        dob_str = f"{date.today().year - age:04d}-01-01"
        p.dob_enc = cu.cipher.enc(dob_str)
        updated = True
    if gender:
        if gender in ("male", "female"):
            p.sex = gender
        p.gender_enc = cu.cipher.enc(gender)
        updated = True
    if updated:
        await db.commit()

    # If they also asked a health question in the same message, save profile silently and let answer() proceed
    if _SYMPTOM_GUARD.search(text or "") or len((text or "").split()) > 14:
        return {"name": name, "age": age, "gender": gender, "reply": None}

    if lang == "bn":
        reply = "ধন্যবাদ, আমি আপনার প্রোফাইল তথ্য আলাদাভাবে সংরক্ষণ করেছি। এবার বলুন, আজ আপনার শরীর কেমন লাগছে বা পরিবারের কারও জন্য কিছু জানতে চাইছেন?"
    elif lang == "hi":
        reply = "धन्यवाद, मैंने आपकी प्रोफ़ाइल जानकारी अलग से सुरक्षित कर ली है। अब बताइए, आज आपकी तबीयत कैसी है, या परिवार के किसी सदस्य के बारे में पूछना चाहते हैं?"
    else:
        reply = "Thank you, I've saved your profile details so your health notes stay separate from any family members you ask about. How are you feeling today, or who can I help you with?"

    return {"name": name, "age": age, "gender": gender, "reply": reply}
