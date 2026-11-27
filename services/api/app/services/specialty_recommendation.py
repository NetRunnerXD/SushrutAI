"""SpecialtyRecommendationService: Context-sensitive medical specialty matching & referral prioritization."""
from __future__ import annotations

from typing import Any

from app.clinical.routing import RoutingDecision


class SpecialtyRecommendationService:
    """Formats specialty routing with primary/secondary order and emergency override."""

    def recommend(self, routing: RoutingDecision, *, lang: str = "en") -> dict[str, Any]:
        clean = (lang or "en").split("-")[0].lower()
        is_emergency = routing.urgency_level == 1

        if is_emergency:
            if clean == "bn":
                first_note = "এটি জরুরি অবস্থা — সাধারণ অ্যাপয়েন্টমেন্টের জন্য অপেক্ষা না করে এখনই নিকটস্থ হাসপাতালের জরুরি বিভাগে (Emergency) যান বা ১০৮/১১২-এ কল করুন।"
            elif clean == "hi":
                first_note = "यह आपातकालीन स्थिति है — सामान्य अपॉइंटमेंट का इंतज़ार न करें, तुरंत नज़दीकी अस्पताल के इमरजेंसी विभाग में जाएं या 108/112 पर कॉल करें।"
            else:
                first_note = "Emergency override — seek immediate Emergency Medicine care or call 108/112 rather than booking a routine appointment."
        elif routing.secondary_departments:
            sec = ", ".join(routing.secondary_departments)
            if clean == "bn":
                first_note = f"প্রথমে {routing.primary_department_local} ({routing.specialty_title}) দেখান; প্রয়োজন হলে এরপর {sec}-এর পরামর্শ নেওয়া যেতে পারে।"
            elif clean == "hi":
                first_note = f"पहले {routing.specialty_title} ({routing.primary_department}) से मिलें; आवश्यकता होने पर बाद में {sec} से परामर्श लिया जा सकता है।"
            else:
                first_note = f"Consult {routing.specialty_title} ({routing.primary_department}) first; {sec} may be considered later if needed."
        else:
            if clean == "bn":
                first_note = f"উপযুক্ত চিকিৎসক: {routing.specialty_title} ({routing.primary_department_local})।"
            elif clean == "hi":
                first_note = f"उपयुक्त विशेषज्ञ: {routing.specialty_title} ({routing.primary_department})।"
            else:
                first_note = f"Recommended specialist: {routing.specialty_title} ({routing.primary_department})."

        priority_level = "emergency" if is_emergency else ("priority" if routing.urgency_level == 2 else "routine")
        return {
            "primary_department": routing.primary_department,
            "hospital_department": f"Department of {routing.primary_department}" if not is_emergency else "Emergency Department",
            "primary_department_local": routing.primary_department_local,
            "specialty": routing.specialty_title,
            "qualification": routing.qualification,
            "reason": routing.routing_reason,
            "secondary_departments": list(routing.secondary_departments),
            "consult_first_guidance": first_note,
            "emergency_override": is_emergency,
            "priority_level": priority_level,
            "referral_urgency": routing.urgency_code,
            "legacy_specialist": routing.legacy_specialist,
        }
