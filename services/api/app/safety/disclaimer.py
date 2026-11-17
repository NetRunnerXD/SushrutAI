DISCLAIMER_VERSION = "2026-11-v1"

DISCLAIMERS = {
    "en": "SushrutAI gives health information only — it is not a diagnosis or prescription. "
          "Always consult a registered medical practitioner. In an emergency call 108 / 112.",
    "hi": "SushrutAI केवल स्वास्थ्य जानकारी देता है — यह निदान या पर्चा नहीं है। "
          "हमेशा पंजीकृत डॉक्टर से सलाह लें। आपातकाल में 108 / 112 पर कॉल करें।",
}

# Hard rules injected into every system prompt and checked post-generation.
PROHIBITIONS = [
    "Never tell the person to take, start, stop or change any medicine, and never name a medicine, brand or dose for "
    "them to take; say 'ask a doctor or pharmacist what you can take' instead.",
    "Never state a diagnosis as certain. Say what the symptoms can match ('your symptoms can be seen with ...', "
    "'this matches ...') and how well they match.",
    "Never give a definitive interpretation of a medical image; describe what is visible and advise a radiologist or "
    "specialist review.",
    "Never help anyone get prescription-only (Schedule H, H1 or X) medicines without a prescription.",
    "Always give practical help: what the person can do now at home, the warning signs that need urgent care, and "
    "when and which doctor to see. Never refuse, never say 'I cannot provide medical advice' or 'I am not a doctor', "
    "and never answer with only 'see a doctor'.",
    "If the SOURCES do not cover the question, answer from well-established general medical knowledge, in general terms.",
]


def disclaimer(lang: str) -> str:
    return DISCLAIMERS.get(lang, DISCLAIMERS["en"])
