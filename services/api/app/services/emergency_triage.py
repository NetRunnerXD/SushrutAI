"""EmergencyTriageService: Immediate deterministic red-flag detection & voice/screen alert builder."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.safety.clinical_safety import evaluate_safety
from app.safety.red_flags import RedFlagResult, check as check_red_flags

_URGENT_BN_BY_RULE = {
    "cardiac_chest_pain": "বুকে ব্যথা বা অস্বস্তি হার্টের সমস্যার লক্ষণ হতে পারে। এখনই বিশ্রাম নিন এবং ১০৮ বা ১১২ নম্বরে কল করুন। নিজে গাড়ি চালাবেন না।",
    "breathing": "গুরুতর শ্বাসকষ্ট একটি জরুরি অবস্থা। এখনই ১০৮ বা ১১২ নম্বরে কল করুন এবং সোজা হয়ে বসে থাকুন।",
    "stroke_fast": "এটি স্ট্রোকের লক্ষণ হতে পারে। কখন উপসর্গ শুরু হয়েছে তা লিখে রাখুন এবং অবিলম্বে ১০৮ বা ১১২ নম্বরে কল করুন।",
    "unconscious_seizure": "অজ্ঞান হওয়া বা খিঁচুনি হলে অবিলম্বে ১০৮ বা ১১২ নম্বরে কল করুন। রোগীকে কাত করে শোয়ান এবং মুখে কিছু দেবেন না।",
    "severe_bleeding": "অতিরিক্ত রক্তপাত বা রক্ত বমি হলে ক্ষতস্থানে চাপ দিয়ে ধরে রাখুন এবং এখনই ১০৮ বা ১১২ নম্বরে কল করুন।",
    "severe_allergy_anaphylaxis": "মুখ, ঠোঁট, জিহ্বা বা গলা ফুলে যাওয়া গুরুতর অ্যালার্জির (অ্যানাফিল্যাক্সিস) লক্ষণ হতে পারে। এখনই ১০৮ বা ১১২ নম্বরে কল করুন।",
    "self_harm": "আপনি একা নন। সাহায্যের জন্য এখনই টেলি-মানস ১৪৪১৬ (বিনামূল্যে, ২৪ ঘণ্টা) বা ১১২ নম্বরে ফোন করুন এবং কাছের বিশ্বস্ত কারও সাথে কথা বলুন।",
}


@dataclass
class EmergencyAssessment:
    triggered: bool
    emergency: bool
    kind: str | None = None
    rules: list[str] = field(default_factory=list)
    call_numbers: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    spoken_alert: str = ""
    screen_title: str = ""
    stop_routine_questioning: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "triggered": self.triggered,
            "emergency": self.emergency,
            "kind": self.kind,
            "rules": self.rules,
            "call_numbers": self.call_numbers,
            "messages": self.messages,
            "spoken_alert": self.spoken_alert,
            "screen_title": self.screen_title,
            "stop_routine_questioning": self.stop_routine_questioning,
        }


class EmergencyTriageService:
    """Runs before routine registration/questioning and overrides all non-emergency flows."""

    def assess(
        self,
        text: str,
        *,
        lang: str = "en",
        age_years: int | float | None = None,
        recent_turns: list[str] | None = None,
    ) -> EmergencyAssessment:
        combined = "\n".join(x for x in [text, *(recent_turns or [])[-2:]] if x)
        rf: RedFlagResult = check_red_flags(combined, lang=lang, age_years=age_years)
        safety = evaluate_safety(combined, lang=lang, age_years=age_years)

        triggered = bool(rf.triggered or safety.escalate_now)
        if not triggered:
            return EmergencyAssessment(triggered=False, emergency=False)

        is_emergency = rf.kind in ("emergency", "mental_health") or safety.escalate_now
        kind = rf.kind or ("emergency" if safety.escalate_now else "urgent")
        nums = list(rf.call_numbers or (["14416", "112"] if kind == "mental_health" else ["108", "112"]))

        msgs = list(rf.messages)
        if lang == "bn":
            bn_extra = [_URGENT_BN_BY_RULE[r] for r in rf.rules if r in _URGENT_BN_BY_RULE]
            if bn_extra:
                msgs = bn_extra + [m for m in msgs if m not in bn_extra]
        if not msgs and safety.emergency_messages:
            msgs = list(safety.emergency_messages)

        spoken = " ".join(msgs[:2]).strip()
        if not spoken:
            if lang == "bn":
                spoken = "এটি জরুরি অবস্থা হতে পারে। দেরি না করে এখনই ১০৮ বা ১১২ নম্বরে কল করুন।"
            elif lang == "hi":
                spoken = "यह आपातकालीन स्थिति हो सकती है। तुरंत 108 या 112 पर कॉल करें।"
            else:
                spoken = "This may be a medical emergency. Please call 108 or 112 immediately."

        title = (
            "জরুরি সতর্কতা — এখনই চিকিৎসা সহায়তা নিন"
            if lang == "bn"
            else ("आपातकालीन चेतावनी — तुरंत चिकित्सा सहायता लें" if lang == "hi" else "Emergency Warning — Seek Immediate Medical Care")
        )
        return EmergencyAssessment(
            triggered=True,
            emergency=bool(is_emergency),
            kind=kind,
            rules=list(rf.rules),
            call_numbers=nums,
            messages=msgs,
            spoken_alert=spoken,
            screen_title=title,
            stop_routine_questioning=bool(is_emergency),
        )
