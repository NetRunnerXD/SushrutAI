"""PatientSummaryService: Automatic compact structured consultation summary with consent & privacy."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clinical.extract import extract_deterministic
from app.clinical.persistence import get_or_create_encounter
from app.models import ChatSession, Encounter, MedicalHistoryEntry, Message
from app.security.auth import CurrentUser
from app.security.consent import active_purposes
from app.services.patient_profile import PatientProfileService

log = logging.getLogger("sushrut.patient_summary")

NOT_PROVIDED = "Not provided"


def _fmt_list(vals: list[str] | None) -> str:
    cleaned = [str(v).strip() for v in (vals or []) if str(v).strip()]
    return ", ".join(dict.fromkeys(cleaned)) if cleaned else NOT_PROVIDED


class PatientSummaryService:
    """Generates, stores (with consent), updates, and deletes compact per-session consultation summaries."""

    def __init__(self) -> None:
        self.profile_svc = PatientProfileService()

    async def build_compact_summary(
        self,
        db: AsyncSession,
        cu: CurrentUser,
        *,
        session_id: str,
        latest_user_text: str = "",
        latest_structured: dict[str, Any] | None = None,
        latest_panel: dict[str, Any] | None = None,
        lang: str = "en",
    ) -> dict[str, Any]:
        snap = await self.profile_svc.load_snapshot(db, cu, session_id=session_id)

        # Aggregate ALL user & assistant messages across the session so earlier symptoms are never discarded.
        msgs = list(
            await db.scalars(
                select(Message).where(Message.session_id == session_id).order_by(Message.created_at)
            )
        )
        user_texts: list[str] = []
        last_structured: dict[str, Any] = dict(latest_structured or {})
        for m in msgs:
            try:
                txt = cu.cipher.dec(m.content_enc) or ""
            except Exception:  # noqa: BLE001 — session messages may be encrypted with account_cipher
                try:
                    txt = cu.account_cipher.dec(m.content_enc) or ""
                except Exception:  # noqa: BLE001
                    txt = ""
            if m.role == "user" and txt.strip():
                user_texts.append(txt.strip())
            elif m.role == "assistant" and isinstance(m.structured, dict) and not latest_structured:
                last_structured = dict(m.structured)

        if latest_user_text.strip() and (not user_texts or user_texts[-1] != latest_user_text.strip()):
            user_texts.append(latest_user_text.strip())

        combined_user_text = "\n".join(user_texts)
        det = extract_deterministic(
            combined_user_text or latest_user_text,
            patient_id=cu.id,
            encounter_id=session_id,
            language=lang,
            raw_transcript=combined_user_text,
        )

        # Preserve chronology: earliest symptom/clinical user message is main problem (skip pure identity onboarding turns)
        clinical_texts = [
            t for t in user_texts
            if not (
                (lambda ext: (ext.name or ext.age is not None or ext.gender or ext.declined_field))(self.profile_svc.extract_name_and_age(t))
                and not extract_deterministic(t, patient_id=cu.id, encounter_id=session_id, language=lang, raw_transcript=t).symptoms
            )
        ]
        chief = (clinical_texts[0] if clinical_texts else (user_texts[0] if user_texts else (latest_user_text.strip() or NOT_PROVIDED)))
        if len(chief) > 180:
            chief = chief[:177] + "..."

        sym_names = [s.name for s in det.symptoms]
        durations = sorted({s.duration for s in det.symptoms if s.duration})
        locations = sorted({loc for s in det.symptoms if (loc := (getattr(s, "body_part", None) or getattr(s, "location", None)))})
        severities = sorted({s.severity for s in det.symptoms if s.severity})
        frequencies = sorted({getattr(s, "frequency", None) for s in det.symptoms if getattr(s, "frequency", None)})

        family_hist = list(
            await db.scalars(
                select(MedicalHistoryEntry).where(
                    MedicalHistoryEntry.user_id == cu.id,
                    MedicalHistoryEntry.category == "family_history",
                )
            )
        )
        fam_list = [cu.cipher.dec(h.title_enc) or "" for h in family_hist]

        causes = list(last_structured.get("possible_conditions") or last_structured.get("possible_explanations") or [])
        cause_names = [str(c.get("name") or c.get("name_en") or "") for c in causes if isinstance(c, dict)]
        cause_names = [n for n in cause_names if n]
        if not cause_names and latest_panel:
            cause_names = [str(d.get("condition") or "") for d in (latest_panel.get("differentials") or []) if d.get("condition")]

        warn_list = list(last_structured.get("watch_for") or [])
        if latest_panel and latest_panel.get("emergency_messages"):
            warn_list = list(latest_panel["emergency_messages"]) + warn_list

        rec_spec = last_structured.get("recommended_specialist") or {}
        doc_name = rec_spec.get("specialty") or last_structured.get("specialist") or NOT_PROVIDED
        doc_reason = rec_spec.get("reason") or (last_structured.get("department_classification") or {}).get("reason") or NOT_PROVIDED

        invs = list(last_structured.get("recommended_tests") or last_structured.get("recommended_investigations") or [])
        inv_names = [str(i.get("test_name") or i.get("investigation_name") or "") for i in invs if isinstance(i, dict)]
        inv_names = [n for n in inv_names if n] or list(last_structured.get("suggested_tests") or [])

        diet = last_structured.get("diet_and_lifestyle") or {}
        food_bits = list(diet.get("suitable_foods") or diet.get("helpful_foods") or [])
        if diet.get("foods_to_limit"):
            food_bits.append("Limit: " + "; ".join(str(x) for x in diet["foods_to_limit"][:2]))
        lifestyle_bits = list(diet.get("lifestyle_recommendations") or diet.get("lifestyle") or [])
        if diet.get("hydration_guidance") or diet.get("hydration"):
            lifestyle_bits.append(str(diet.get("hydration_guidance") or diet.get("hydration")))

        fqs = [str(q) for q in (last_structured.get("follow_up_questions") or []) if str(q).strip()]
        next_steps = list(last_structured.get("self_care") or last_structured.get("tips") or [])
        if last_structured.get("emergency_guidance"):
            next_steps.insert(0, str(last_structured["emergency_guidance"]))

        summary = {
            "session_id": session_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "patient_information": {
                "patient_name": snap.name or NOT_PROVIDED,
                "age": snap.age if snap.age is not None else NOT_PROVIDED,
                "gender": snap.sex or NOT_PROVIDED,
                "location": snap.location or NOT_PROVIDED,
                "first_consultation": snap.first_consultation,
                "preferred_language": lang or snap.preferred_lang or NOT_PROVIDED,
            },
            "chief_complaint": {
                "main_problem": chief,
                "symptom_chronology": user_texts,
            },
            "symptom_details": {
                "duration": _fmt_list(durations),
                "location": _fmt_list(locations),
                "severity": _fmt_list(severities),
                "frequency": _fmt_list(frequencies),
                "associated_symptoms": _fmt_list(sym_names),
            },
            "medical_history": {
                "previous_conditions": _fmt_list(snap.conditions),
                "current_medications": _fmt_list(snap.medications),
                "known_allergies": _fmt_list(snap.allergies),
                "relevant_family_history": _fmt_list(fam_list),
            },
            "ai_assessment": {
                "possible_explanations": _fmt_list(cause_names),
                "important_warning_signs": _fmt_list(warn_list),
                "assessment_limitations": "Preliminary AI health information only; not a confirmed clinical diagnosis.",
            },
            "recommendations": {
                "recommended_doctor": doc_name,
                "reason_for_referral": doc_reason,
                "possible_investigations": _fmt_list(inv_names),
                "general_food_guidance": _fmt_list(food_bits),
                "lifestyle_guidance": _fmt_list(lifestyle_bits),
            },
            "medication_information": {
                "medicines_discussed": _fmt_list(snap.medications),
                "precautions": "Consult a doctor or pharmacist before starting, stopping, or changing any dose.",
                "questions_for_clinician": "Confirm dose, duration, and interactions with current medicines or allergies.",
            },
            "follow_up": {
                "questions_requiring_clarification": _fmt_list(fqs),
                "recommended_next_steps": _fmt_list(next_steps[:4]),
            },
        }
        return summary

    async def save_if_consented(
        self,
        db: AsyncSession,
        cu: CurrentUser,
        *,
        session_id: str,
        summary: dict[str, Any],
        channel: str = "text",
        lang: str = "en",
    ) -> bool:
        purposes = await active_purposes(db, cu.account_id)
        if "core_service" not in purposes or "history_storage" not in purposes:
            return False
        sess = await db.get(ChatSession, session_id)
        if sess is None or sess.user_id != cu.id:
            return False
        enc = await get_or_create_encounter(db, cu, chat_session_id=session_id, channel=channel, language=lang)
        existing: dict[str, Any] = {}
        if enc.summary_enc:
            try:
                existing = json.loads(cu.cipher.dec(enc.summary_enc) or "{}")
            except Exception:  # noqa: BLE001
                existing = {}
        existing["compact_summary"] = summary
        existing.setdefault("main_complaint", summary["chief_complaint"]["main_problem"])
        existing["patient_name"] = summary["patient_information"]["patient_name"]
        existing["age"] = summary["patient_information"]["age"]
        existing["language"] = lang
        enc.summary_enc = cu.cipher.enc(json.dumps(existing, ensure_ascii=False))
        await db.commit()
        return True


def build_7_sections(
    *,
    patient: dict[str, Any] | None,
    compact_summary: dict[str, Any] | None,
    structured: dict[str, Any] | None,
    lang: str = "en",
) -> dict[str, Any]:
    """Assemble the 7 separated UI consultation sections (only used once recommendations are unlocked)."""
    clean = (lang or "en").split("-")[0].lower()
    st = structured or {}
    comp = compact_summary or {}
    pat = patient or {}

    p_info = comp.get("patient_information") or {}
    c_info = comp.get("chief_complaint") or {}
    spec = st.get("recommended_specialist") or {}
    diet = st.get("diet_and_lifestyle") or {}

    titles = {
        "bn": {
            "s1": "রোগীর প্রোফাইল",
            "s2": "বর্তমান সমস্যা",
            "s3": "ক্লিনিক্যাল মূল্যায়ন",
            "s4": "পরামর্শকৃত ডাক্তার / বিশেষজ্ঞ",
            "s5": "প্রয়োজনীয় পরীক্ষা-নিরীক্ষা",
            "s6": "পরবর্তী করণীয়",
            "s7": "খাদ্যতালিকা ও সতর্কতা",
        },
        "hi": {
            "s1": "रोगी प्रोफ़ाइल",
            "s2": "वर्तमान समस्या",
            "s3": "क्लिनिकल मूल्यांकन",
            "s4": "अनुशंसित डॉक्टर / विशेषज्ञ",
            "s5": "अनुशंसित जांच",
            "s6": "अगले कदम",
            "s7": "आहार और सावधानियां",
        },
        "en": {
            "s1": "Patient Profile",
            "s2": "Current Problem",
            "s3": "Clinical Assessment",
            "s4": "Recommended Doctor / Specialist",
            "s5": "Recommended Tests",
            "s6": "Next Steps",
            "s7": "Diet & Precautions",
        },
    }.get(clean, {
        "s1": "Patient Profile",
        "s2": "Current Problem",
        "s3": "Clinical Assessment",
        "s4": "Recommended Doctor / Specialist",
        "s5": "Recommended Tests",
        "s6": "Next Steps",
        "s7": "Diet & Precautions",
    })

    name_val = pat.get("name") or p_info.get("patient_name") or "unavailable"
    age_val = pat.get("age") if pat.get("age") is not None else (p_info.get("age") if p_info.get("age") is not None else "unavailable")
    gender_val = pat.get("sex") or p_info.get("gender") or "unavailable"

    causes = st.get("possible_conditions") or []
    tests = st.get("recommended_tests") or []
    next_steps = st.get("self_care") or (comp.get("follow_up") or {}).get("recommended_next_steps") or []
    if isinstance(next_steps, str):
        next_steps = [next_steps] if next_steps != "Not provided" else []

    return {
        "section_1_patient_profile": {
            "title": titles["s1"],
            "name": name_val,
            "age": age_val,
            "gender": gender_val,
        },
        "section_2_current_problem": {
            "title": titles["s2"],
            "main_complaint": c_info.get("main_problem") or "Not specified",
            "duration": c_info.get("duration") or "Not specified",
            "key_symptoms": c_info.get("symptom_chronology") or [],
            "severity": c_info.get("severity") or "Not specified",
            "associated_symptoms": c_info.get("associated_symptoms") or [],
        },
        "section_3_clinical_assessment": {
            "title": titles["s3"],
            "summary": st.get("reply") or "",
            "possible_causes": causes,
            "uncertainties": st.get("uncertainties") or [],
        },
        "section_4_recommended_specialist": {
            "title": titles["s4"],
            "primary_specialist": spec.get("primary_specialist") or st.get("specialist") or "",
            "primary_department": spec.get("primary_department") or "",
            "secondary_specialists": spec.get("secondary_specialists") or [],
            "reason": spec.get("reason") or "",
            "urgency_note": spec.get("urgency_note") or "",
        },
        "section_5_recommended_tests": {
            "title": titles["s5"],
            "items": tests,
            "clinician_note": st.get("investigations_note") or "",
        },
        "section_6_next_steps": {
            "title": titles["s6"],
            "steps": list(next_steps),
        },
        "section_7_diet_and_precautions": {
            "title": titles["s7"],
            "dietary_guidance": list(diet.get("recommended_foods") or []),
            "foods_to_limit": list(diet.get("foods_to_limit") or []),
            "hydration": diet.get("hydration") or "",
            "lifestyle_precautions": list(diet.get("lifestyle_precautions") or []),
            "watch_for": list(st.get("watch_for") or []),
        },
    }
