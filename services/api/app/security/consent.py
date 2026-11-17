"""Purpose-scoped consent (DPDP Act 2023 s.6 / DPDP Rules 2025 r.3)."""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Consent

NOTICE_VERSION = "2026-11-v1"

PURPOSES: dict[str, dict] = {
    "core_service": {
        "required": True,
        "en": "Process the health information you enter (symptoms, profile, prescriptions) on this "
              "device/server to answer your questions. Processed by local AI models only.",
        "hi": "आपके द्वारा दर्ज स्वास्थ्य जानकारी (लक्षण, प्रोफ़ाइल, पर्चे) को आपके प्रश्नों का उत्तर देने के लिए "
              "इसी डिवाइस/सर्वर पर संसाधित करना। केवल स्थानीय AI मॉडल द्वारा।",
    },
    "history_storage": {
        "required": False,
        "en": "Save your chats, prescriptions and reports in your encrypted health history.",
        "hi": "आपकी बातचीत, पर्चे और रिपोर्ट आपके एन्क्रिप्टेड स्वास्थ्य इतिहास में सहेजना।",
    },
    "cloud_speech": {
        "required": False,
        "en": "Send voice audio to India-hosted speech services (Bhashini, Sarvam AI) when local "
              "speech recognition is unavailable. Audio is not stored by SushrutAI.",
        "hi": "स्थानीय वाक् पहचान उपलब्ध न होने पर आवाज़ को भारत में होस्ट की गई सेवाओं (भाषिणी, सर्वम AI) को भेजना।",
    },
    "cloud_llm_deidentified": {
        "required": False,
        "en": "For complex questions, send a de-identified summary (no name, phone, ID numbers, or "
              "images) to a free cloud AI service (Groq, servers outside India).",
        "hi": "जटिल प्रश्नों के लिए, पहचान हटाया गया सारांश (नाम, फ़ोन, आईडी या चित्र नहीं) एक मुफ़्त क्लाउड AI "
              "सेवा (Groq, भारत के बाहर सर्वर) को भेजना।",
    },
    "model_improvement": {
        "required": False,
        "en": "Use your corrections to prescription readings (de-identified) to improve SushrutAI's models.",
        "hi": "पर्चा पढ़ने में आपके सुधारों (पहचान हटाकर) का उपयोग SushrutAI मॉडल सुधारने के लिए करना।",
    },
}


async def active_purposes(db: AsyncSession, user_id: str) -> set[str]:
    rows = await db.scalars(
        select(Consent.purpose).where(Consent.user_id == user_id, Consent.withdrawn_at.is_(None))
    )
    return set(rows)


async def require(db: AsyncSession, user_id: str, purpose: str) -> None:
    if purpose not in await active_purposes(db, user_id):
        raise HTTPException(403, f"Consent '{purpose}' is required for this action")
