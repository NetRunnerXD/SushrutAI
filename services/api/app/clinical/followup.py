"""Personalized Hybrid Follow-Up Question Engine.

Combines:
A. Deterministic clinical rules (emergency screening, mandatory red-flag checks, critical missing info).
B. Context-aware filtering (never repeats questions already answered or facts already extracted).
C. Simple, accessible multilingual phrasing (English, spoken Bengali, Hindi, plus IndicTrans2 fallback).

Always generates ONE clear, understandable question at a time and persists Q&A into `followup_questions`.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clinical.persistence import emit_event
from app.clinical.schema import ClinicalExtractionPayload
from app.memory.context import MemoryContext
from app.models import FollowUpQuestion
from app.safety.clinical_safety import SafetyAssessment
from app.security.auth import CurrentUser

log = logging.getLogger("sushrut.clinical.followup")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass
class QuestionTemplate:
    key: str
    category: str
    priority: int  # 1 = highest (safety), 5 = lowest
    reason: str
    en: str
    bn: str
    hi: str
    cluster: str = "general"  # dizziness | fever | respiratory | gi | headache | general


# Deterministic clinical question bank (prioritized)
_QUESTIONS: tuple[QuestionTemplate, ...] = (
    # --- Safety screening for dizziness / orthostatic / weakness ---
    QuestionTemplate(
        key="rf_chest_breath",
        category="safety",
        priority=1,
        cluster="dizziness",
        reason="Screen for cardiac or respiratory emergency associated with dizziness/weakness",
        en="Are you having any chest pain or difficulty breathing right now?",
        bn="আপনার কি এখন বুকে ব্যথা বা শ্বাস নিতে কষ্ট হচ্ছে?",
        hi="क्या आपको अभी सीने में दर्द या सांस लेने में कोई दिक्कत हो रही है?",
    ),
    QuestionTemplate(
        key="rf_fainting",
        category="safety",
        priority=1,
        cluster="dizziness",
        reason="Screen for syncope / loss of consciousness",
        en="Have you ever fainted or lost consciousness?",
        bn="আপনি কি কখনো মাথা ঘুরে অজ্ঞান হয়ে পড়েছেন?",
        hi="क्या आप कभी चक्कर खाकर बेहोश हुए हैं?",
    ),
    QuestionTemplate(
        key="rf_vision_dark",
        category="safety",
        priority=2,
        cluster="dizziness",
        reason="Assess orthostatic presyncope (vision darkening on standing)",
        en="Does your vision become dark or blurry when you stand up?",
        bn="উঠে দাঁড়ালে কি আপনার চোখের সামনে অন্ধকার বা ঝাপসা লাগে?",
        hi="खड़े होने पर क्या आपकी आँखों के सामने अंधेरा छा जाता है?",
    ),
    # --- Safety screening for fever ---
    QuestionTemplate(
        key="rf_fever_meningitis_dengue",
        category="safety",
        priority=1,
        cluster="fever",
        reason="Screen for meningitis or severe dengue warning signs",
        en="Do you have a stiff neck, severe headache, bleeding gums, or red spots on your skin?",
        bn="আপনার কি ঘাড় শক্ত হয়ে গেছে, খুব মাথাব্যথা বা শরীরের কোথাও লাল দাগ বা রক্তপাত হচ্ছে?",
        hi="क्या आपको गर्दन में अकड़न, तेज़ सिरदर्द, या शरीर पर लाल चकत्ते या खून आने की दिक्कत है?",
    ),
    # --- Safety screening for GI / vomiting / loose motion ---
    QuestionTemplate(
        key="rf_gi_dehydration",
        category="safety",
        priority=1,
        cluster="gi",
        reason="Screen for severe dehydration or GI bleeding",
        en="Are you seeing any blood in vomit or stool, or unable to keep water down?",
        bn="বমি বা পায়খানার সাথে কি রক্ত যাচ্ছে, অথবা জল খেলেও কি বমি হয়ে যাচ্ছে?",
        hi="क्या उल्टी या दस्त में खून आ रहा है, या पानी पीने पर भी उल्टी हो जाती है?",
    ),
    # --- Unidentified medicine / discrepancy clarification ---
    QuestionTemplate(
        key="clarify_unidentified_med",
        category="medication",
        priority=2,
        cluster="general",
        reason="Patient mentioned a medicine they could not identify",
        en="You mentioned a medicine whose name wasn't clear. Can you upload a photo of the medicine strip or ask a local pharmacist to read it?",
        bn="আপনি যে ওষুধটির কথা বললেন তার নাম স্পষ্ট নয়। অনুগ্রহ করে ওষুধের পাতার একটি ছবি দিন অথবা কাছের ওষুধের দোকান বা স্বাস্থ্যকর্মীর সাহায্য নিন।",
        hi="आपने जिस दवा का ज़िक्र किया उसका नाम साफ़ नहीं है। क्या आप दवा के पत्ते की फोटो भेज सकते हैं या किसी फार्मासिस्ट से पढ़वा सकते हैं?",
    ),
    QuestionTemplate(
        key="clarify_discrepancy",
        category="clarification",
        priority=2,
        cluster="general",
        reason="Resolve contradictory statement without discarding prior history",
        en="Earlier you gave a slightly different timeline for this symptom. Could you confirm roughly when it actually started?",
        bn="আগে এই সমস্যার শুরুর সময় নিয়ে একটু আলাদা কথা বলেছিলেন। অনুগ্রহ করে নিশ্চিত করে বলবেন, এটি ঠিক কবে থেকে শুরু হয়েছে?",
        hi="पहले आपने इस समस्या के शुरू होने का समय थोड़ा अलग बताया था। कृपया बताएं कि यह असल में कब से शुरू हुई थी?",
    ),
    # --- Core missing clinical details ---
    QuestionTemplate(
        key="onset_duration",
        category="onset",
        priority=2,
        cluster="general",
        reason="Onset / duration of symptom is unknown",
        en="When did this problem first start?",
        bn="এই সমস্যাটি ঠিক কবে থেকে শুরু হয়েছে?",
        hi="यह समस्या पहली बार कब शुरू हुई थी?",
    ),
    QuestionTemplate(
        key="episode_duration_freq",
        category="duration",
        priority=2,
        cluster="dizziness",
        reason="Assess whether dizziness lasts seconds vs minutes and frequency",
        en="When it spins, does it last a few seconds or several minutes?",
        bn="মাথা ঘোরা শুরু হলে তা কি কয়েক সেকেন্ড থাকে, নাকি কয়েক মিনিট ধরে চলে?",
        hi="जब चक्कर आता है तो क्या यह कुछ सेकंड रहता है या कई मिनट तक चलता है?",
    ),
    QuestionTemplate(
        key="severity_impact",
        category="severity",
        priority=3,
        cluster="general",
        reason="Symptom severity is unknown",
        en="How severe does it feel right now — mild, moderate, or stopping you from daily work?",
        bn="এখন সমস্যাটি কতটা বেশি মনে হচ্ছে — হালকা, মাঝারি, নাকি দৈনন্দিন কাজ করতে পারছেন না?",
        hi="अभी यह तकलीफ़ कितनी तेज़ है — हल्की, मध्यम, या काम करने में दिक्कत हो रही है?",
    ),
    QuestionTemplate(
        key="hydration_eating",
        category="hydration",
        priority=3,
        cluster="general",
        reason="Assess oral intake & dehydration risk",
        en="Have you been eating and drinking water normally?",
        bn="আপনি কি ঠিকমতো খাবার খাচ্ছেন এবং পর্যাপ্ত জল পান করছেন?",
        hi="क्या आप सामान्य रूप से खाना खा रहे हैं और पानी पी रहे हैं?",
    ),
    QuestionTemplate(
        key="current_meds",
        category="medication",
        priority=4,
        cluster="general",
        reason="Medication history for this episode not yet known",
        en="Are you currently taking any medicines or home remedies for this?",
        bn="আপনি কি এখন এর জন্য কোনো ওষুধ বা ঘরোয়া কিছু খাচ্ছেন?",
        hi="क्या आप अभी इसके लिए कोई दवा ले रहे हैं?",
    ),
)

_QUESTION_BY_KEY = {q.key: q for q in _QUESTIONS}


def _cluster_for(symptoms: list[str]) -> set[str]:
    names = " ".join(symptoms).lower()
    clusters = {"general"}
    if any(w in names for w in ("dizz", "vertigo", "head heaviness", "weakness", "malaise", "faint")):
        clusters.add("dizziness")
    if any(w in names for w in ("fever", "chills")):
        clusters.add("fever")
    if any(w in names for w in ("diarrhea", "vomit", "nausea", "abdominal")):
        clusters.add("gi")
    if any(w in names for w in ("cough", "shortness of breath", "chest")):
        clusters.add("respiratory")
    return clusters


def _already_satisfied(
    key: str,
    extraction: ClinicalExtractionPayload,
    mem: MemoryContext,
) -> bool:
    neg_set = {s.name.lower() for s in extraction.negated_symptoms}
    pos_set = {s.name.lower() for s in extraction.symptoms}
    all_text = (extraction.normalized_transcript or "").lower()

    if key == "rf_chest_breath":
        return (
            "chest pain" in neg_set or "shortness of breath" in neg_set
            or "chest pain" in pos_set or "shortness of breath" in pos_set
            or bool(re.search(r"chest pain|breath|বুকে ব্যথা|শ্বাস", all_text))
        )
    if key == "rf_fainting":
        return "fainting" in neg_set or "fainting" in pos_set or bool(re.search(r"faint|unconscious|অজ্ঞান|বেহোश", all_text))
    if key == "rf_vision_dark":
        return "vision darkening" in neg_set or "vision darkening" in pos_set or bool(re.search(r"vision|dark|চোখ", all_text))
    if key == "onset_duration":
        # Satisfied if ANY active symptom has known duration/onset
        has_dur = any(s.duration or (s.temporal and not s.temporal.is_temporal_uncertain) for s in extraction.symptoms)
        has_mem_dur = any(s.get("duration") or s.get("onset") for s in mem.short_term.get("active_symptoms", []))
        return has_dur or has_mem_dur
    if key == "episode_duration_freq":
        return any(bool(s.frequency and ("second" in s.frequency or "minute" in s.frequency or "continuous" in s.frequency)) for s in extraction.symptoms) or bool(re.search(r"\b(?:seconds?|minutes?|সেকেন্ড|মিনিট)\b", all_text))
    if key == "severity_impact":
        has_sev = any(s.severity != "unknown" or s.functional_impact for s in extraction.symptoms)
        has_mem_sev = any(s.get("severity") not in (None, "unknown") or s.get("functional_impact") for s in mem.short_term.get("active_symptoms", []))
        return has_sev or has_mem_sev
    if key == "hydration_eating":
        return bool(extraction.functional_context.hydration) or bool(re.search(r"eat|drink|water|জল|খাচ্ছি|পানি", all_text))
    if key == "current_meds":
        return bool(extraction.medications) or bool(mem.long_term.get("medications")) or bool(re.search(r"no medic|not taking any|কোনো ওষুধ না", all_text))
    if key == "clarify_unidentified_med":
        return not any(m.unidentified for m in extraction.medications) and not mem.unidentified_meds
    if key == "clarify_discrepancy":
        return not mem.discrepancies
    return False


def _localize(q: QuestionTemplate, lang: str) -> str:
    if lang == "bn":
        return q.bn
    if lang == "hi":
        return q.hi
    return q.en


async def record_followup_answer(
    db: AsyncSession,
    cu: CurrentUser,
    *,
    encounter_id: str,
    answer_text: str,
    extraction: ClinicalExtractionPayload,
) -> list[str]:
    """If an open pending question exists for this encounter, mark it answered with the patient's reply
    and emit FOLLOWUP_COMPLETED. Also resolves any pending questions directly answered by extraction."""
    pending = list(await db.scalars(
        select(FollowUpQuestion)
        .where(FollowUpQuestion.user_id == cu.id, FollowUpQuestion.encounter_id == encounter_id, FollowUpQuestion.status == "pending")
        .order_by(FollowUpQuestion.asked_at.asc())
    ))
    if not pending:
        return []

    completed_keys: list[str] = []
    now = _now()
    # Primary pending question (most recently asked) receives the turn's answer text
    latest = pending[-1]
    latest.answer_text_enc = cu.cipher.enc(answer_text[:500])
    latest.status = "answered"
    latest.answered_at = now
    completed_keys.append(latest.question_key)
    await emit_event(
        db,
        cu,
        encounter_id=encounter_id,
        event_type="FOLLOWUP_COMPLETED",
        entity_type="followup_question",
        entity_id=latest.id,
        source="patient_report",
        verification_status="patient_reported",
        payload={"question_key": latest.question_key, "answer_summary": answer_text[:200]},
        occurred_at=now,
    )

    # Mark any other pending questions satisfied by the same rich reply as answered too
    for fq in pending[:-1]:
        if fq.question_key == "onset_duration" and any(s.duration for s in extraction.symptoms):
            fq.answer_text_enc = cu.cipher.enc(answer_text[:500])
            fq.status = "answered"
            fq.answered_at = now
            completed_keys.append(fq.question_key)

    await db.commit()
    return completed_keys


async def next_followup_question(
    db: AsyncSession,
    cu: CurrentUser,
    *,
    encounter_id: str,
    extraction: ClinicalExtractionPayload,
    mem: MemoryContext,
    safety: SafetyAssessment,
    lang: str = "en",
) -> dict | None:
    """Delegate follow-up selection to AdaptiveClinicalInterviewEngine (dynamic, hypothesis-driven, uncapped)."""
    from app.clinical.interview import AdaptiveClinicalInterviewEngine

    res = await AdaptiveClinicalInterviewEngine().evaluate_and_next_question(
        db,
        cu,
        encounter_id=encounter_id,
        user_text=extraction.normalized_transcript or extraction.raw_transcript or "",
        extraction=extraction,
        mem=mem,
        safety=safety,
        lang=lang,
    )
    q = res.get("question")
    if q is not None:
        q["interview_outcome"] = res.get("outcome")
    return q
