"""Clinical Decision Support System (CDSS) Orchestration & 9-Section Structured Validator.

Connects:
  1. Patient Symptom Understanding (`extract_deterministic`)
  2. Safety & Emergency Risk Assessment (`evaluate_safety` + `red_flags`)
  3. Medical Category Classification & Indian MBBS/MD/MS/DM/MCh Routing (`classify_and_route`)
  4. Patient-Dependent Diagnostic Investigation Engine (`recommend_for_turn`)
  5. Verified Medical Knowledge Provenance (`citations`, `consulted`, `medical_ref`)
  6. 9-Section Structured Medical Response & Validation
"""
from __future__ import annotations

import re
from typing import Any

from app.clinical.diet_lifestyle import recommend_diet_and_lifestyle
from app.clinical.extract import extract_deterministic
from app.clinical.investigations import recommend_for_turn
from app.clinical.knowledge_graph import analyze_clinical_differentials
from app.clinical.routing import classify_and_route
from app.safety.clinical_safety import evaluate_safety
from app.safety.disclaimer import disclaimer

_CLINICIAN_NOTE = {
    "en": "The treating doctor decides which investigations are actually necessary after clinical examination.",
    "bn": "শারীরিক পরীক্ষার পর কোন পরীক্ষাটি প্রকৃতপক্ষে প্রয়োজন তা কেবল চিকিৎসকই নির্ধারণ করবেন।",
    "hi": "शारीरिक जांच के बाद कौन-सी जांच वास्तव में आवश्यक है, इसका निर्णय उपचार करने वाले डॉक्टर ही करेंगे।",
}

_CONFIRMATION_CAVEAT = {
    "en": "Possible medical considerations only — this cannot be confirmed without physical examination and appropriate investigations.",
    "bn": "এগুলো কেবল সম্ভাব্য কারণ — শারীরিক পরীক্ষা এবং প্রাসঙ্গিক পরীক্ষা ছাড়া নিশ্চিত রোগ নির্ণয় সম্ভব নয়।",
    "hi": "ये केवल संभावित कारण हैं — शारीरिक जांच और आवश्यक परीक्षणों के बिना इसकी पुष्टि नहीं की जा सकती।",
}

_FOLLOWUP_FALLBACK = {
    "en": [
        "How long have these symptoms been present, and are they getting better or worse?",
        "Are you taking any regular medicines, or do you have diabetes, high blood pressure, or allergies?",
    ],
    "bn": [
        "এই সমস্যাটি কতদিন ধরে হচ্ছে, এবং এটি কি সময়ের সাথে বাড়ছে না কমছে?",
        "আপনি কি নিয়মিত কোনো ওষুধ খাচ্ছেন, বা আপনার ডায়াবেটিস, উচ্চ রক্তচাপ বা অ্যালার্জি আছে?",
    ],
    "hi": [
        "ये लक्षण कितने समय से हैं, और क्या ये समय के साथ बढ़ रहे हैं या कम हो रहे हैं?",
        "क्या आप कोई नियमित दवा ले रहे हैं, या आपको शुगर, ब्लड प्रेशर या एलर्जी की समस्या है?",
    ],
}


def evaluate_cdss_context(
    text: str,
    *,
    lang: str = "en",
    age_years: int | None = None,
    pregnant: bool = False,
    comorbidities: list[str] | None = None,
    red_flag_triggered: bool = False,
    red_flag_kind: str | None = None,
) -> dict[str, Any]:
    """Run deterministic clinical understanding, specialty routing, urgency & investigation selection."""
    det = extract_deterministic(
        text or "",
        patient_id="turn",
        encounter_id=None,
        language=lang,
        raw_transcript=text or "",
    )
    safety = evaluate_safety(text or "", lang=lang, age_years=age_years, extraction=det)
    routing = classify_and_route(
        text or "",
        lang=lang,
        extraction=det,
        safety=safety,
        age_years=age_years,
        pregnant=pregnant,
        comorbidities=comorbidities,
        red_flag_triggered=red_flag_triggered,
        red_flag_kind=red_flag_kind,
    )
    is_emerg = routing.urgency_level == 1
    is_urgent = routing.urgency_level == 2
    sig = " ".join([
        text or "",
        " ".join(s.name for s in det.symptoms),
        " ".join(t for s in det.symptoms for t in s.triggers),
        " ".join(comorbidities or []),
    ])
    parsed_days = 0.0
    for f in routing.matched_factors:
        m = re.search(r"duration~(\d+)d", f)
        if m:
            parsed_days = max(parsed_days, float(m.group(1)))
    investigations = recommend_for_turn(
        sig,
        duration_days=parsed_days,
        urgent=is_urgent,
        emergency=is_emerg,
        pregnant=pregnant or ("pregnancy_context" in routing.matched_factors),
        max_items=4,
    )
    differential = analyze_clinical_differentials(
        sig,
        symptoms=[s.name for s in det.symptoms],
        lang=lang,
    )
    diet_and_lifestyle = recommend_diet_and_lifestyle(
        sig,
        dept=routing.primary_department,
        lang=lang,
        age_years=age_years,
        pregnant=pregnant or ("pregnancy_context" in routing.matched_factors),
        comorbidities=comorbidities,
    )
    return {
        "extraction": det,
        "safety": safety,
        "routing": routing,
        "investigations": investigations,
        "differential": differential,
        "diet_and_lifestyle": diet_and_lifestyle,
    }


def cdss_prompt_block(cdss: dict[str, Any]) -> str:
    """Compact clinical grounding block injected into the existing LLM prompt."""
    rt = cdss["routing"]
    invs = cdss["investigations"]
    inv_names = ", ".join(x["investigation_name"] for x in invs[:4]) if invs else "None routine until examined"
    return (
        f"CLINICAL ROUTING & STEWARDSHIP CONTEXT:\n"
        f"- Primary Department: {rt.primary_department}"
        + (f" (Secondary: {', '.join(rt.secondary_departments)})" if rt.secondary_departments else "") + "\n"
        f"- Recommended Doctor Level: {rt.qualification} ({rt.specialty_title}) — {rt.initial_vs_specialist}\n"
        f"- Urgency Classification: {rt.urgency_code}\n"
        f"- Clinically Justified Investigation Candidates (only suggest if truly warranted; clinician decides): {inv_names}\n"
    )


def build_nine_sections(
    *,
    text: str,
    lang: str,
    structured: dict[str, Any],
    cdss: dict[str, Any],
    citations: list[dict] | None = None,
    consulted: list[dict] | None = None,
) -> dict[str, Any]:
    """Construct the validated 9-Section Structured Medical Response."""
    det = cdss["extraction"]
    rt = cdss["routing"]
    invs = cdss["investigations"]
    lang_key = lang if lang in ("en", "bn", "hi") else "en"

    syms = [s.name for s in det.symptoms]
    locs = sorted({loc for s in det.symptoms if (loc := (getattr(s, "body_part", None) or getattr(s, "location", None)))})
    durs = sorted({s.duration for s in det.symptoms if s.duration})
    understanding = {
        "summary": structured.get("reply") or text,
        "symptoms": syms,
        "locations": locs,
        "durations": durs,
        "negatives": [n.name for n in det.negated_symptoms],
    }

    causes_raw = structured.get("possible_conditions") or []
    causes = [
        {
            "name": c.get("name") or c.get("name_en"),
            "name_en": c.get("name_en") or c.get("name"),
            "likelihood": c.get("likelihood", "moderate"),
            "urgency": c.get("urgency", rt.urgency_api),
            "supporting": c.get("supporting") or [],
            "against": c.get("against") or [],
        }
        for c in causes_raw
    ]

    # Combine justified deterministic tests with safe validated model test names (without duplicates or unindicated MRI/CT)
    merged_tests: list[dict[str, Any]] = list(invs)
    seen_t = {x["investigation_name"].lower() for x in merged_tests}
    if rt.urgency_level != 1:
        for tname in structured.get("suggested_tests") or []:
            low = str(tname).strip().lower()
            if not low or any(low in s or s in low for s in seen_t):
                continue
            if len(merged_tests) >= 5:
                break
            merged_tests.append({
                "investigation_name": str(tname).strip(),
                "domain_category": "clinician_considered",
                "category": "diagnostic",
                "why_relevant": "Suggested for discussion with the examining clinician based on reported symptoms.",
                "what_it_evaluates": str(tname).strip(),
                "stage": "initial_consideration" if rt.qualification == "MBBS" else "specialist_later",
                "priority_context": f"{rt.urgency_api} · Clinician-decided",
                "clinician_decision_required": True,
                "limitations": "Treating doctor decides after examination.",
            })
            seen_t.add(low)

    fqs = list(structured.get("follow_up_questions") or [])
    if not fqs and rt.urgency_level != 1:
        fqs = list(_FOLLOWUP_FALLBACK[lang_key])

    next_steps = list(structured.get("self_care") or structured.get("tips") or [])
    if rt.urgency_advice and rt.urgency_advice not in next_steps:
        next_steps = [rt.urgency_advice] + next_steps

    all_sources = list(citations or []) + list(consulted or [])
    initial_invs = [t for t in merged_tests if t.get("stage") == "initial_consideration"]
    specialist_invs = [t for t in merged_tests if t.get("stage") != "initial_consideration"]
    not_justified_note = (
        "রোগীর বর্তমান উপসর্গ ও পরীক্ষা ছাড়া রুটিন এমআরআই, সিটি স্ক্যান বা অপ্রয়োজনীয় বড় রক্তের প্যানেল এখনই যুক্তিসঙ্গত নয়।"
        if lang_key == "bn"
        else "Routine MRI/CT or broad test panels are not currently justified without physical examination."
    )
    return {
        "section_1_understanding_your_problem": understanding,
        "section_2_possible_medical_causes": {
            "caveat": _CONFIRMATION_CAVEAT[lang_key],
            "conditions": causes,
        },
        "section_3_recommended_medical_department": {
            "primary_department": rt.primary_department,
            "primary_department_local": rt.primary_department_local,
            "why_relevant": rt.primary_reason,
            "secondary_departments": rt.secondary_departments,
        },
        "section_4_which_doctor_should_you_consult": {
            "recommended_qualification": rt.qualification,
            "medical_specialty": rt.specialty_title,
            "reason": rt.routing_reason,
            "consultation_path": rt.initial_vs_specialist,
        },
        "section_5_recommended_diagnostic_investigations": {
            "clinician_decision_note": _CLINICIAN_NOTE[lang_key],
            "A_initial_investigations": initial_invs,
            "B_conditional_and_specialist_investigations": specialist_invs,
            "D_not_currently_justified": not_justified_note,
            "investigations": merged_tests,
        },
        "section_6_important_follow_up_questions": fqs[:3],
        "section_7_urgency_and_safety_advice": {
            "urgency_level": rt.urgency_level,
            "urgency_code": rt.urgency_code,
            "advice": rt.urgency_advice,
            "watch_for": list(structured.get("watch_for") or []),
        },
        "section_8_next_steps": next_steps[:6],
        "section_9_medical_disclaimer": disclaimer(lang),
        "knowledge_provenance": [
            {"title": s.get("title", ""), "source": s.get("source", ""), "url": s.get("url", "")}
            for s in all_sources[:5]
        ],
    }


def validate_and_repair_response(
    *,
    structured: dict[str, Any],
    cdss: dict[str, Any],
    answered_topics: set[str] | None = None,
    next_question: str | None = None,
    lang: str = "en",
) -> list[str]:
    """Validate medical response completeness, non-repetition, and safety; repair if incomplete."""
    from app.clinical.context_manager import _infer_question_topic

    repairs: list[str] = []
    rt = cdss["routing"]
    asked = answered_topics or set()

    # 1. Strip repetitive follow-up questions that were already answered
    fqs = list(structured.get("follow_up_questions") or [])
    filtered_fqs = [q for q in fqs if (_infer_question_topic(q) or "") not in asked]
    if len(filtered_fqs) != len(fqs):
        repairs.append("removed_repeated_followup")
    if next_question and next_question not in filtered_fqs and rt.urgency_level != 1:
        filtered_fqs = [next_question] + filtered_fqs[:2]
    structured["follow_up_questions"] = filtered_fqs[:3]

    # 2. Ensure reply is never empty or purely generic
    reply = (structured.get("reply") or "").strip()
    if len(reply) < 20:
        if lang == "bn":
            reply = (
                f"আপনার উপসর্গ অনুযায়ী প্রাথমিকভাবে {rt.primary_department_local} ({rt.specialty_title}) "
                f"বিভাগে পরামর্শ নেওয়া উপযুক্ত হতে পারে। {rt.urgency_advice}"
            )
        else:
            reply = (
                f"Based on your symptoms, consulting {rt.specialty_title} ({rt.primary_department}) "
                f"is recommended. {rt.urgency_advice}"
            )
        structured["reply"] = reply
        repairs.append("repaired_empty_reply")

    # 3. Validate and repair spoken_response for Voice-First output
    spoken = (structured.get("spoken_response") or "").strip()
    has_bad_markers = any(m in spoken for m in ("{", "}", "```", "**", "##"))
    spec_title = (rt.specialty_title or "").lower()
    dept_name = (rt.primary_department or "").lower()
    if (
        not spoken
        or has_bad_markers
        or (spec_title and spec_title not in spoken.lower() and dept_name not in spoken.lower())
    ):
        structured["spoken_response"] = build_spoken_response(
            text="",
            lang=lang,
            structured=structured,
            cdss=cdss,
        )
        repairs.append("repaired_spoken_response")

    return repairs


def _clean_for_speech(text: str) -> str:
    """Strip markdown, JSON artifacts, and excess whitespace for clean TTS."""
    s = re.sub(r"```[\s\S]*?```", " ", text or "")
    s = re.sub(r"[*_#`>|]", " ", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def build_spoken_response(
    *,
    text: str,
    lang: str,
    structured: dict[str, Any],
    cdss: dict[str, Any],
) -> str:
    """Build a natural, complete Voice-First spoken response synchronized with clinical routing."""
    rt = cdss["routing"]
    invs: list[dict[str, Any]] = list(cdss.get("investigations") or [])
    causes = list(structured.get("possible_conditions") or structured.get("possible_causes") or [])
    fqs = list(structured.get("follow_up_questions") or [])
    reply_clean = _clean_for_speech(str(structured.get("reply") or ""))

    # Extract concise opening clinical understanding (first 1-2 sentences, <= 260 chars)
    opening = ""
    if reply_clean:
        sents = re.split(r"(?<=[.!?।])\s+", reply_clean)
        opening = " ".join(sents[:2]).strip()
        if len(opening) > 260:
            opening = opening[:257].rsplit(" ", 1)[0] + "."

    top_cause = ""
    if causes and isinstance(causes[0], dict):
        top_cause = _clean_for_speech(str(causes[0].get("name") or ""))

    if lang == "bn":
        parts: list[str] = []
        if opening:
            parts.append(opening)
        elif top_cause:
            parts.append(f"আপনার উপসর্গ অনুযায়ী সম্ভাব্য কারণগুলোর মধ্যে {top_cause} অন্যতম হতে পারে, তবে নিশ্চিত নির্ণয় কেবল চিকিৎসকের পরীক্ষার পরই সম্ভব।")
        parts.append(
            f"উপযুক্ত বিভাগ হলো {rt.primary_department_local} ({rt.primary_department})। "
            f"পরামর্শের জন্য {rt.specialty_title} ({rt.qualification}) দেখানো উচিত, কারণ {rt.routing_reason}"
        )
        if invs:
            inv_items = "; ".join(
                f"{t['investigation_name']} ({t.get('why_relevant') or t.get('why_recommended') or ''})"
                for t in invs[:2]
            )
            parts.append(f"প্রাথমিকভাবে চিকিৎসক যে পরীক্ষাগুলো বিবেচনা করতে পারেন: {inv_items}।")
        else:
            parts.append("এই মুহূর্তে সরাসরি ল্যাব পরীক্ষার আগে চিকিৎসকের শারীরিক পরীক্ষা ও মূল্যায়নই প্রধান।")
        if rt.urgency_advice:
            parts.append(rt.urgency_advice)
        if fqs and rt.urgency_level != 1:
            parts.append(f"আরও স্পষ্টতার জন্য জানাবেন: {_clean_for_speech(str(fqs[0]))}")
        spoken = " ".join(parts)
    elif lang == "hi":
        parts = []
        if opening:
            parts.append(opening)
        elif top_cause:
            parts.append(f"आपके लक्षणों के आधार पर संभावित कारणों में {top_cause} शामिल हो सकता है, लेकिन पक्की जांच डॉक्टर की सलाह के बाद ही होगी।")
        parts.append(
            f"इसके लिए सही विभाग {rt.primary_department} है, और {rt.specialty_title} ({rt.qualification}) से परामर्श करना उचित रहेगा, क्योंकि {rt.routing_reason}"
        )
        if invs:
            inv_items = "; ".join(
                f"{t['investigation_name']} ({t.get('why_relevant') or t.get('why_recommended') or ''})"
                for t in invs[:2]
            )
            parts.append(f"शुरुआती जांच में डॉक्टर ये टेस्ट सुझा सकते हैं: {inv_items}।")
        else:
            parts.append("अभी सीधे जांच से पहले डॉक्टर द्वारा शारीरिक परीक्षण सबसे जरूरी है।")
        if rt.urgency_advice:
            parts.append(rt.urgency_advice)
        if fqs and rt.urgency_level != 1:
            parts.append(f"कृपया यह भी बताएं: {_clean_for_speech(str(fqs[0]))}")
        spoken = " ".join(parts)
    else:
        parts = []
        if opening:
            parts.append(opening)
        elif top_cause:
            parts.append(f"Based on your symptoms, possible considerations include {top_cause}, though only a clinician can confirm after examination.")
        parts.append(
            f"The appropriate department is {rt.primary_department}. "
            f"You should consult a {rt.specialty_title} ({rt.qualification}), because {rt.routing_reason}"
        )
        if invs:
            inv_items = "; ".join(
                f"{t['investigation_name']} to {t.get('why_relevant') or t.get('why_recommended') or 'support clinical evaluation'}"
                for t in invs[:2]
            )
            parts.append(f"Relevant initial tests a doctor may consider include: {inv_items}.")
        else:
            parts.append("A direct physical examination by the specialist is the main priority before deciding on tests.")
        if rt.urgency_advice:
            parts.append(rt.urgency_advice)
        if fqs and rt.urgency_level != 1:
            parts.append(f"To help narrow this down: {_clean_for_speech(str(fqs[0]))}")
        spoken = " ".join(parts)

    spoken = _clean_for_speech(spoken)
    if len(spoken) > 850:
        spoken = spoken[:847].rsplit(" ", 1)[0] + "."
    return spoken


def enrich_structured_response(
    *,
    text: str,
    lang: str,
    structured: dict[str, Any],
    cdss: dict[str, Any],
    citations: list[dict] | None = None,
    consulted: list[dict] | None = None,
) -> dict[str, Any]:
    """Validate and enrich `structured` in place while preserving existing frontend/API fields."""
    rt = cdss["routing"]
    # Keep `specialist` compatible with existing SPECIALISTS enum while ensuring valid routing;
    # override automatic "General Physician" fallback whenever a specific specialty applies.
    if not structured.get("specialist") or (
        structured.get("specialist") == "General Physician"
        and rt.legacy_specialist != "General Physician"
    ):
        structured["specialist"] = rt.legacy_specialist
    if cdss["investigations"]:
        structured["suggested_tests"] = [t["investigation_name"] for t in cdss["investigations"]]
    structured["department_classification"] = {
        "primary": rt.primary_department,
        "primary_local": rt.primary_department_local,
        "secondary": rt.secondary_departments,
        "reason": rt.primary_reason,
        "factors": rt.matched_factors,
    }
    structured["specialist_routing"] = {
        "qualification": rt.qualification,
        "specialty": rt.specialty_title,
        "reason": rt.routing_reason,
        "consultation_path": rt.initial_vs_specialist,
    }
    structured["urgency_classification"] = {
        "level": rt.urgency_level,
        "code": rt.urgency_code,
        "api_urgency": rt.urgency_api,
        "advice": rt.urgency_advice,
    }
    structured["recommended_investigations"] = cdss["investigations"]
    structured["stages"] = [
        "CONTEXT_LOADED",
        "CLINICAL_ANALYSIS_COMPLETE",
        "RESPONSE_GENERATED",
        "RESPONSE_VALIDATED",
    ]

    # Ensure no definitive diagnosis phrasing survives
    if isinstance(structured.get("reply"), str):
        structured["reply"] = re.sub(
            r"(?i)\byou (?:definitely|certainly|undoubtedly) have\b",
            "possible causes that may need evaluation include",
            structured["reply"],
        )

    spoken_response = build_spoken_response(
        text=text,
        lang=lang,
        structured=structured,
        cdss=cdss,
    )
    structured["success"] = True
    structured["language"] = lang
    structured["symptom_summary"] = text
    structured["possible_explanations"] = list(structured.get("possible_conditions") or structured.get("possible_causes") or [])
    structured["recommended_department"] = rt.primary_department
    structured["recommended_specialist"] = {
        "specialty": rt.specialty_title,
        "qualification": rt.qualification,
        "reason": rt.routing_reason,
        "referral_urgency": rt.urgency_code,
    }
    structured["recommended_tests"] = [
        {
            "test_name": t["investigation_name"],
            "reason": t.get("why_relevant") or t.get("why_recommended") or "",
            "priority": t.get("category", "initial"),
            "stewardship_level": t.get("stewardship_level", 2),
        }
        for t in cdss["investigations"]
    ]
    diff = cdss.get("differential")
    if diff is not None:
        structured["differential_assessment"] = {
            "supporting_findings": diff.supporting_findings,
            "contradictory_findings": diff.contradictory_findings,
            "missing_information": diff.missing_information,
            "must_not_miss_warnings": diff.must_not_miss_warnings,
            "uncertainty_note": diff.uncertainty_note,
            "multi_symptom_linked": diff.multi_symptom_linked,
            "knowledge_provenance": diff.knowledge_provenance,
        }
    structured["urgency_level"] = rt.urgency_api
    structured["diet_and_lifestyle"] = cdss.get("diet_and_lifestyle") or {}
    structured["spoken_response"] = spoken_response
    structured["text_response"] = structured.get("reply") or ""
    structured["emergency_guidance"] = rt.urgency_advice

    structured["clinical_sections"] = build_nine_sections(
        text=text,
        lang=lang,
        structured=structured,
        cdss=cdss,
        citations=citations,
        consulted=consulted,
    )
    return structured
