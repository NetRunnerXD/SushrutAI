"""Module 1: Adaptive Question Understanding Engine.

Classifies patient queries into 7 clinical/educational categories:
  - urgent_health_concern
  - symptom_question
  - medical_report_question
  - medication_question
  - preventive_healthcare
  - follow_up_question
  - general_health_question

Distinguishes educational questions from personal clinical assessments, handles
misspelled/incomplete/colloquial/multilingual inputs (English, Bengali, Hindi, Banglish),
preserves Conversation Language Lock, and never recommends tests/doctors for unrelated
general-knowledge questions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from app.clinical.extract import extract_deterministic
from app.clinical.family_memory import detect_relationship_mention
from app.safety.red_flags import check as red_flag_check
from app.speech.normalize import resolve_conversation_language

AEGIQueryType = Literal[
    "urgent_health_concern",
    "symptom_question",
    "medical_report_question",
    "medication_question",
    "preventive_healthcare",
    "follow_up_question",
    "general_health_question",
]

_REPORT_Q = re.compile(
    r"(?i)\b(?:lab\s*report|blood\s*test|hba1c|cbc|creatinine|bilirubin|tsh|x[- ]?ray|ultrasound|mri|ct\s*scan|report\s+shows?)\b|"
    r"রিপোর্ট|ল্যাব\s*রিপোর্ট|रिपोर्ट"
)
_MED_Q = re.compile(
    r"(?i)\b(?:medicine|medication|tablet|pill|capsule|syrup|paracetamol|metformin|telma|dolo|side\s*effect|drug\s+interaction|dosage|dose)\b|"
    r"ওষুধ|বড়ি|ট্যাবলেট|দবা|दवा|गोली"
)
_PREVENTIVE_Q = re.compile(
    r"(?i)\b(?:prevent|prevention|vaccine|vaccination|screening|healthy\s+diet|exercise|lifestyle|how\s+to\s+avoid|immunity)\b|"
    r"প্রতিরোধ|টিকা|ব্যায়াম|সুষম\s*খাবার|रोकथाम|टीका"
)
_EDUCATIONAL_Q = re.compile(
    r"(?i)^(?:what\s+is|what\s+are|why\s+does|how\s+does|explain|define|tell\s+me\s+about)\b|"
    r"(?:কী|কিভাবে|কেন\s+হয়)\b|(?:क्या\s+है|कैसे\s+होता\s+है)"
)
_PERSONAL_PRONOUNS = re.compile(
    r"(?i)\b(?:i\s+have|i\s+am|i'm|my\s+|amar\s+|amr\s+|mujhe\s+|mera\s+|meri\s+|mere\s+)\b|আমার|আমি|मुझे|मेरा|मेरी"
)
_FOLLOWUP_Q = re.compile(
    r"(?i)^(?:yes|no|yeah|nah|ok|okay|3\s*days|since\s+yesterday|darale|kal\s+theke|হ্যাঁ|না|কাল\s+থেকে|हां|नहीं)\b|"
    r"\b(?:what\s+about|how\s+long\s+will|still\s+having|also\s+feeling)\b"
)


@dataclass
class QuestionUnderstanding:
    query_type: AEGIQueryType
    is_educational: bool
    is_personal_assessment: bool
    should_recommend_tests_or_doctor: bool
    active_language: str
    bcp47: str
    language_switched: bool
    extracted_symptoms: list[str] = field(default_factory=list)
    route_target: str = "general_rag"

    def as_dict(self) -> dict:
        return {
            "query_type": self.query_type,
            "is_educational": self.is_educational,
            "is_personal_assessment": self.is_personal_assessment,
            "should_recommend_tests_or_doctor": self.should_recommend_tests_or_doctor,
            "active_language": self.active_language,
            "bcp47": self.bcp47,
            "language_switched": self.language_switched,
            "extracted_symptoms": self.extracted_symptoms,
            "route_target": self.route_target,
        }


def understand_question(
    text: str,
    *,
    locked_lang: str | None = None,
    ui_lang: str = "en",
    history: list[dict] | None = None,
    has_active_symptoms_in_history: bool = False,
) -> QuestionUnderstanding:
    """Classify query intent, educational vs personal scope, language lock, and backend route."""
    t = (text or "").strip()
    lang_dec = resolve_conversation_language(t, locked_lang=locked_lang, ui_lang=ui_lang)
    lang = lang_dec.active_language

    rf = red_flag_check(t, lang=lang)
    if rf.triggered and rf.kind in ("emergency", "mental_health"):
        return QuestionUnderstanding(
            query_type="urgent_health_concern",
            is_educational=False,
            is_personal_assessment=True,
            should_recommend_tests_or_doctor=True,
            active_language=lang,
            bcp47=lang_dec.bcp47,
            language_switched=lang_dec.switched,
            extracted_symptoms=[],
            route_target="emergency_escalation",
        )

    ext = extract_deterministic(t, patient_id="understanding", language=lang)
    syms = [s.name for s in ext.symptoms]
    family_rel = detect_relationship_mention(t)
    if not syms and family_rel is not None:
        syms = [h.normalized_name for h in ext.medical_history if h.category == "family_history" and h.normalized_name]
    has_personal = bool(_PERSONAL_PRONOUNS.search(t)) or (family_rel is not None)
    looks_educational = bool(_EDUCATIONAL_Q.search(t)) and not has_personal

    if _REPORT_Q.search(t):
        qtype: AEGIQueryType = "medical_report_question"
        route = "lab_report_intelligence"
        need_clinical = has_personal
    elif _MED_Q.search(t) and not syms:
        qtype = "medication_question"
        route = "medication_safety"
        need_clinical = has_personal
    elif _PREVENTIVE_Q.search(t) and not syms:
        qtype = "preventive_healthcare"
        route = "preventive_guidance"
        need_clinical = False
    elif (history or has_active_symptoms_in_history) and _FOLLOWUP_Q.search(t) and not looks_educational:
        qtype = "follow_up_question"
        route = "adaptive_interview"
        need_clinical = True
    elif syms and not looks_educational:
        qtype = "symptom_question"
        route = "clinical_reasoning_12layer"
        need_clinical = True
    else:
        qtype = "general_health_question"
        route = "evidence_grounded_rag"
        need_clinical = False

    return QuestionUnderstanding(
        query_type=qtype,
        is_educational=not need_clinical,
        is_personal_assessment=need_clinical,
        should_recommend_tests_or_doctor=need_clinical,
        active_language=lang,
        bcp47=lang_dec.bcp47,
        language_switched=lang_dec.switched,
        extracted_symptoms=syms,
        route_target=route,
    )
