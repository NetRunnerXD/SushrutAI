"""AdaptiveQuestionService: Complaint-specific, red-flag-first, non-repetitive follow-up questioning."""
from __future__ import annotations

import re
from typing import Any

from app.clinical.context_manager import ConsultationContext, build_consultation_context
from app.clinical.question_engine import QuestionPlan, plan_next_question
from app.clinical.routing import RoutingDecision

_MEAL_Q_BANK = (
    "meal_relation",
    "Does the stomach pain get worse after eating or on an empty stomach?",
    "খাবার খাওয়ার পরে ব্যথা বাড়ে, নাকি খালি পেটে বেশি হয়?",
    "क्या यह दर्द खाना खाने के बाद बढ़ता है, या खाली पेट ज्यादा होता है?",
)

_SEVERITY_Q_BANK = (
    "severity",
    "How severe is the discomfort on a scale from mild to severe, and does it disturb your sleep or daily work?",
    "ব্যথা বা অস্বস্তি কতটা তীব্র—হালকা, মাঝারি নাকি খুব বেশি, এবং এতে কি ঘুম বা কাজে সমস্যা হচ্ছে?",
    "यह तकलीफ़ कितनी तेज़ है—हल्की, मध्यम या बहुत तेज़, और क्या इससे नींद या रोज़मर्रा के काम में दिक्कत होती है?",
)

_HEADACHE_Q_BANK: tuple[tuple[str, str, str, str], ...] = (
    (
        "location",
        "Which part of your head is hurting — forehead, temples, one side, or the back of the head?",
        "আপনার মাথার কোন অংশে ব্যথা হচ্ছে — কপালের দিকে, এক পাশে, না কি মাথার পেছনে?",
        "आपके सिर के किस हिस्से में दर्द हो रहा है — माथे, एक तरफ़ या पीछे?",
    ),
    (
        "frequency",
        "Is the headache constant, or does it come and go?",
        "ব্যথাটা কি সারাক্ষণ থাকে, নাকি মাঝে মাঝে হয়?",
        "क्या यह दर्द लगातार बना रहता है, या बीच-बीच में आता है?",
    ),
    (
        "warning_signs",
        "Along with this, do you have blurred vision, nausea, or dizziness?",
        "এর সঙ্গে কি চোখে ঝাপসা দেখা, বমি বমি ভাব অথবা মাথা ঘোরার মতো কিছু হচ্ছে?",
        "क्या इसके साथ धुंधला दिखना, मतली या चक्कर जैसा महसूस हो रहा है?",
    ),
)


def _pick(lang: str, en: str, bn: str, hi: str) -> str:
    return bn if lang == "bn" else hi if lang == "hi" else en


class AdaptiveQuestionService:
    """Generates up to 2–3 relevant follow-ups, asks 1 at a time in voice mode, and never repeats answered topics."""

    def build_context(
        self,
        history: list[dict[str, Any]] | None,
        current_text: str,
        *,
        lang: str = "en",
    ) -> ConsultationContext:
        ctx = build_consultation_context(history, current_text, lang=lang)
        sig = ctx.merged_clinical_text.lower()
        if re.search(r"(?:after eating|empty stomach|after meal|খালি পেটে|খাওয়ার পর|খাবার খাওয়ার পর|খাবার|খাওয়া|खाना|खाली पेट)", sig):
            ctx.answered_topics.add("meal_relation")
        if re.search(r"(?:mild|moderate|severe|unbearable|scale|\b\d+/10\b|হালকা|মাঝারি|তীব্র|অসহ্য|हल्का|तेज़|असहनीय)", sig):
            ctx.answered_topics.add("severity")
        if re.search(r"(?:allergy|allergic|অ্যালার্জি|এলर्जी)", sig):
            ctx.answered_topics.add("allergies")
        if re.search(r"(?:forehead|temple|one side|back of head|কপাল|এক পাশ|মাথার পেছন|माथे|एक तरफ)", sig):
            ctx.answered_topics.add("location")
        if re.search(r"(?:constant|continuous|comes and goes|intermittent|সারাক্ষণ|মাঝে মাঝে|একটানা|लगातार|बीच-बीच)", sig):
            ctx.answered_topics.add("frequency")
        return ctx

    def plan(
        self,
        ctx: ConsultationContext,
        routing: RoutingDecision,
        *,
        lang: str = "en",
        channel: str = "text",
        emergency_stop: bool = False,
        existing_questions: list[str] | None = None,
    ) -> dict[str, Any]:
        if emergency_stop or routing.urgency_level == 1:
            return {
                "should_ask": False,
                "sufficient_information": True,
                "primary_question": None,
                "questions": [],
                "topic": None,
                "reason": "Emergency signs require immediate care — routine questioning stopped.",
            }

        qp: QuestionPlan = plan_next_question(ctx, routing, lang=lang)
        candidates: list[str] = []
        if qp.should_ask and qp.question:
            candidates.append(qp.question)

        dept = routing.primary_department
        sig = ctx.merged_clinical_text.lower()
        # Headache-specific adaptive sequence: location -> frequency -> associated red flags
        if "headache" in ctx.symptoms or re.search(r"মাথাব্যথা|মাথা ব্যথা|সিরে? दर्द|सिरदर्द|headache", sig):
            for topic, q_en, q_bn, q_hi in _HEADACHE_Q_BANK:
                if topic not in ctx.answered_topics and len(candidates) < 3:
                    q_txt = _pick(lang, q_en, q_bn, q_hi)
                    if q_txt not in candidates:
                        candidates.append(q_txt)
        # Add complaint-specific extra questions (up to 3 total) for non-answered topics
        if dept in ("Gastroenterology", "Hepatology", "General Surgery") or "abdominal pain" in ctx.symptoms or "পেটে" in sig:
            if "meal_relation" not in ctx.answered_topics:
                q_meal = _pick(lang, _MEAL_Q_BANK[1], _MEAL_Q_BANK[2], _MEAL_Q_BANK[3])
                if q_meal not in candidates:
                    candidates.append(q_meal)
        if "severity" not in ctx.answered_topics and len(candidates) < 3:
            q_sev = _pick(lang, _SEVERITY_Q_BANK[1], _SEVERITY_Q_BANK[2], _SEVERITY_Q_BANK[3])
            if q_sev not in candidates:
                candidates.append(q_sev)

        for eq in (existing_questions or []):
            clean = (eq or "").strip()
            if clean and clean not in candidates and not self._already_answered_text(clean, ctx):
                candidates.append(clean)

        # On the first symptom turn (followup_turn_count == 0), always ask at least 1 clarifying question
        if ctx.followup_turn_count == 0 and not candidates:
            candidates.append(_pick(lang, _SEVERITY_Q_BANK[1], _SEVERITY_Q_BANK[2], _SEVERITY_Q_BANK[3]))

        # In voice conversations ask ONE focused question at a time; text may display up to 3.
        limit = 1 if channel == "voice" else 3
        picked = candidates[:limit] if (qp.should_ask or ctx.followup_turn_count == 0) and candidates else []
        primary = picked[0] if picked else None
        return {
            "should_ask": bool(primary) and (qp.should_ask or ctx.followup_turn_count == 0),
            "sufficient_information": qp.sufficient_information,
            "primary_question": primary,
            "questions": picked,
            "topic": qp.topic,
            "reason": qp.reason,
        }

    def _already_answered_text(self, q: str, ctx: ConsultationContext) -> bool:
        low = q.lower()
        if "duration" in ctx.answered_topics and re.search(r"how long|when did|কতদিন|কবে|कितने दिन|कब से", low):
            return True
        if "location" in ctx.answered_topics and re.search(r"where|কোন জায়গায়|কোন জায়গায়|किस हिस्से", low):
            return True
        if "meal_relation" in ctx.answered_topics and re.search(r"empty stomach|after eating|খালি পেটে|খাওয়ার", low):
            return True
        if "severity" in ctx.answered_topics and re.search(r"severe|mild|তীব্র|হালকা|तेज़", low):
            return True
        return False
