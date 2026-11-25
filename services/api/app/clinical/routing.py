"""Clinical Specialty Classification, Indian MBBS/MD/MS/DM/MCh Routing, and 4-Level Urgency Engine.

Evaluates symptom combinations, duration, patient age/pregnancy/comorbidities, severity, and red flags
(never single-keyword routing alone) to determine:
  1. Primary & secondary medical departments (27+ supported specialties).
  2. Appropriate Indian medical qualification level (MBBS vs MD/MS/DNB vs DM/MCh).
  3. 4-level clinical urgency classification aligned with emergency red-flag rules.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from app.clinical.schema import ClinicalExtractionPayload
from app.safety.clinical_safety import SafetyAssessment

DEPARTMENTS: tuple[str, ...] = (
    "General Medicine",
    "Internal Medicine",
    "General Surgery",
    "Orthopaedics",
    "Cardiology",
    "Neurology",
    "Neurosurgery",
    "Gastroenterology",
    "Hepatology",
    "Nephrology",
    "Urology",
    "Pulmonology",
    "Dermatology",
    "ENT",
    "Ophthalmology",
    "Gynaecology",
    "Obstetrics",
    "Paediatrics",
    "Psychiatry",
    "Endocrinology",
    "Oncology",
    "Rheumatology",
    "Infectious Disease",
    "Haematology",
    "Emergency Medicine",
    "Physical Medicine and Rehabilitation",
    "Other",
)

# Maps our 27+ clinical departments onto the existing UI `SPECIALISTS` enum in orchestrator.py
_DEPT_TO_LEGACY_SPECIALIST: dict[str, str] = {
    "General Medicine": "General Physician",
    "Internal Medicine": "General Physician",
    "General Surgery": "General Physician",
    "Orthopaedics": "Orthopaedician",
    "Cardiology": "Cardiologist",
    "Neurology": "Neurologist",
    "Neurosurgery": "Neurologist",
    "Gastroenterology": "Gastroenterologist",
    "Hepatology": "Gastroenterologist",
    "Nephrology": "Nephrologist",
    "Urology": "Urologist",
    "Pulmonology": "Pulmonologist",
    "Dermatology": "Dermatologist",
    "ENT": "ENT Specialist",
    "Ophthalmology": "Ophthalmologist",
    "Gynaecology": "Gynaecologist",
    "Obstetrics": "Gynaecologist",
    "Paediatrics": "Paediatrician",
    "Psychiatry": "Psychiatrist",
    "Endocrinology": "Endocrinologist",
    "Oncology": "Oncologist",
    "Rheumatology": "Rheumatologist",
    "Infectious Disease": "General Physician",
    "Haematology": "General Physician",
    "Emergency Medicine": "Emergency Department",
    "Physical Medicine and Rehabilitation": "Orthopaedician",
    "Dentistry": "Dentist",
    "Other": "General Physician",
}

_BN_DEPT: dict[str, str] = {
    "General Medicine": "জেনারেল মেডিসিন (সাধারণ চিকিৎসা)",
    "Internal Medicine": "ইন্টারনাল মেডিসিন",
    "General Surgery": "জেনারেল সার্জারি",
    "Orthopaedics": "অর্থোপেডিকস (হাড় ও অস্থিসন্ধি)",
    "Cardiology": "কার্ডিওলজি (হৃদরোগ বিভাগ)",
    "Neurology": "নিউরোলজি (স্নায়ুরোগ বিভাগ)",
    "Neurosurgery": "নিউরোসার্জারি",
    "Gastroenterology": "গ্যাস্ট্রোএন্টারোলজি (পরিপাকতন্ত্র)",
    "Hepatology": "হেপাটোলজি (লিভার বিভাগ)",
    "Nephrology": "নেফ্রোলজি (কিডনি বিভাগ)",
    "Urology": "ইউরোলজি (মূত্রতন্ত্র)",
    "Pulmonology": "পালমোনোলজি (বক্ষব্যাধি ও শ্বাসযন্ত্র)",
    "Dermatology": "ডার্মাটোলজি (চর্মরোগ বিভাগ)",
    "ENT": "ইএনটি (নাক, কান ও গলা)",
    "Ophthalmology": "অফথ্যালমোলজি (চক্ষু বিভাগ)",
    "Gynaecology": "গাইনোকোলজি (স্ত্রীরোগ বিভাগ)",
    "Obstetrics": "অবস্টেট্রিকস (প্রসূতি বিভাগ)",
    "Paediatrics": "পেডিয়াট্রিকস (শিশুরোগ বিভাগ)",
    "Psychiatry": "সাইকিয়াট্রি (মনোরোগ ও মানসিক স্বাস্থ্য)",
    "Endocrinology": "এন্ডোক্রাইনোলজি (হরমোন ও ডায়াবেটিস)",
    "Oncology": "অনকোলজি (ক্যান্সার বিভাগ)",
    "Rheumatology": "রিউমাটোলজি (বাত ও অটোইমিউন রোগ)",
    "Infectious Disease": "সংক্রামক রোগ বিভাগ",
    "Haematology": "হেমাটোলজি (রক্তরোগ বিভাগ)",
    "Emergency Medicine": "জরুরি বিভাগ (Emergency)",
    "Physical Medicine and Rehabilitation": "ফিজিক্যাল মেডিসিন ও রিহ্যাবিলিটেশন",
}

URGENCY_LEVELS = {
    1: {"code": "LEVEL 1 — EMERGENCY", "api_urgency": "emergency",
        "en": "Immediate emergency medical attention needed (call 108 / 112 or go to the nearest Emergency Department now).",
        "bn": "এখনই জরুরি চিকিৎসা প্রয়োজন (১০৮ / ১১২ নম্বরে কল করুন বা নিকটতম জরুরি বিভাগে যান)।",
        "hi": "तुरंत आपातकालीन चिकित्सा सहायता लें (108 / 112 पर कॉल करें या नजदीकी इमरजेंसी में जाएँ)।"},
    2: {"code": "LEVEL 2 — URGENT MEDICAL EVALUATION", "api_urgency": "see_doctor_24h",
        "en": "Prompt medical evaluation needed within 24 hours; do not delay if symptoms worsen.",
        "bn": "২৪ ঘণ্টার মধ্যে দ্রুত চিকিৎসকের মূল্যায়ন প্রয়োজন; উপসর্গ বাড়লে দেরি করবেন না।",
        "hi": "24 घंटे के भीतर डॉक्टर से जांच कराना आवश्यक है; लक्षण बढ़ें तो देर न करें।"},
    3: {"code": "LEVEL 3 — ROUTINE SPECIALIST CONSULTATION", "api_urgency": "see_doctor_week",
        "en": "Schedule a non-emergency consultation with the recommended doctor/specialist this week.",
        "bn": "জরুরি না হলেও এই সপ্তাহেই প্রস্তাবিত চিকিৎসক বা বিশেষজ্ঞের পরামর্শ নিন।",
        "hi": "इस सप्ताह के भीतर संबंधित डॉक्टर या विशेषज्ञ से परामर्श लें।"},
    4: {"code": "LEVEL 4 — GENERAL MEDICAL ASSESSMENT", "api_urgency": "self_care",
        "en": "Begin with an MBBS primary-care assessment if symptoms persist or bother you; monitor for warning signs.",
        "bn": "উপসর্গ থাকলে প্রাথমিক মূল্যায়নের জন্য এমবিবিএস (MBBS) চিকিৎসকের পরামর্শ দিয়ে শুরু করুন এবং সতর্কতা চিহ্ন নজরে রাখুন।",
        "hi": "लक्षण बने रहने पर प्राथमिक जांच के लिए एमबीबीएस (MBBS) डॉक्टर से मिलें और चेतावनी के संकेतों पर ध्यान रखें।"},
}


@dataclass
class RoutingDecision:
    primary_department: str
    primary_department_local: str
    primary_reason: str
    secondary_departments: list[str]
    qualification: str  # MBBS | MD | MS | DM | MCh
    specialty_title: str
    routing_reason: str
    initial_vs_specialist: str  # "initial_mbbs_assessment" | "direct_specialist_evaluation" | "emergency_care"
    urgency_level: int
    urgency_code: str
    urgency_api: str
    urgency_advice: str
    legacy_specialist: str
    matched_factors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "primary_department": self.primary_department,
            "primary_department_local": self.primary_department_local,
            "primary_reason": self.primary_reason,
            "secondary_departments": self.secondary_departments,
            "qualification": self.qualification,
            "specialty_title": self.specialty_title,
            "routing_reason": self.routing_reason,
            "initial_vs_specialist": self.initial_vs_specialist,
            "urgency_level": self.urgency_level,
            "urgency_code": self.urgency_code,
            "urgency_api": self.urgency_api,
            "urgency_advice": self.urgency_advice,
            "legacy_specialist": self.legacy_specialist,
            "matched_factors": self.matched_factors,
        }


def _extract_days(text: str, extraction: ClinicalExtractionPayload | None) -> float:
    max_days = 0.0
    if extraction:
        for s in extraction.symptoms:
            dtxt = str(s.duration or "")
            if m := re.search(r"(\d+(?:\.\d+)?)\s*(day|week|month|year)", dtxt, re.I):
                val = float(m.group(1))
                u = m.group(2).lower()
                mult = 365 if u.startswith("y") else 30 if u.startswith("m") else 7 if u.startswith("w") else 1
                max_days = max(max_days, val * mult)
    low = (text or "").lower()
    if re.search(r"অনেকদিন|বহুদিন|মাস|বছর|long time|chronic|months?|years?", low):
        max_days = max(max_days, 21.0)
    for m in re.finditer(r"(\d+)\s*(?:days?|din|দিন|दिन)", low):
        max_days = max(max_days, float(m.group(1)))
    for m in re.finditer(r"(\d+)\s*(?:weeks?|সপ্তাহ|hafta|सप्ताह)", low):
        max_days = max(max_days, float(m.group(1)) * 7.0)
    if re.search(r"সপ্তাহ|weeks?|hafta|सप्ताह", low):
        max_days = max(max_days, 7.0)
    return max_days


def _pick_local(lang: str, en: str, bn: str, hi: str) -> str:
    if lang == "bn":
        return bn
    if lang == "hi":
        return hi
    return en


def classify_and_route(
    text: str,
    *,
    lang: str = "en",
    extraction: ClinicalExtractionPayload | None = None,
    safety: SafetyAssessment | None = None,
    age_years: int | None = None,
    pregnant: bool = False,
    comorbidities: list[str] | None = None,
    red_flag_triggered: bool = False,
    red_flag_kind: str | None = None,
) -> RoutingDecision:
    """Classify medical department, select MBBS/MD/MS/DM/MCh level, and assign 4-level urgency."""
    sig_parts = [text or ""]
    if extraction:
        sig_parts.extend(s.name for s in extraction.symptoms)
        sig_parts.extend((getattr(s, "body_part", None) or getattr(s, "location", None) or "") for s in extraction.symptoms)
        sig_parts.extend(t for s in extraction.symptoms for t in s.triggers)
        sig_parts.extend((getattr(h, "normalized_name", None) or getattr(h, "description", "") or "") for h in extraction.medical_history)
    if comorbidities:
        sig_parts.extend(comorbidities)
    sig = unicodedata.normalize("NFC", " ".join(sig_parts).lower())

    days = _extract_days(text, extraction)
    chronic = days >= 14.0
    subacute = days >= 3.0
    _child_age_m = re.search(r"\b(\d+)\s*(year|yr|month)s?\s*old\b", sig)
    _parsed_child_age = (
        _child_age_m is not None
        and (_child_age_m.group(2).lower().startswith("month") or int(_child_age_m.group(1)) < 16)
    )
    is_child = (
        (age_years is not None and age_years < 16)
        or _parsed_child_age
        or bool(re.search(r"\b(child|baby|infant|toddler|kid|paediatric|pediatric|newborn)\b|বাচ্চা|শিশু|बच्च[ाे]", sig))
    )
    is_pregnant = pregnant or bool(re.search(r"\bpregnan|trimester|prenatal|antenatal\b|গর্ভবতী|প্রেগন্যান্ট|गर्भवती", sig))
    is_severe = bool(re.search(r"\bsevere|unbearable|worsening|intense|sudden|blackout|faint\b|তীব্র|অসহ্য|অজ্ঞান|तेज़", sig))

    factors: list[str] = []
    if days > 0:
        factors.append(f"duration~{int(days)}d")
    if is_child:
        factors.append("paediatric_context")
    if is_pregnant:
        factors.append("pregnancy_context")
    if is_severe:
        factors.append("high_severity")

    # 1. LEVEL 1 EMERGENCY OVERRIDE
    if (red_flag_triggered and red_flag_kind in ("emergency", "mental_health")) or (safety and safety.escalate_now):
        if red_flag_kind == "mental_health" or re.search(r"suicid|self[- ]harm|আত্মহত্যা|মরতে ইচ্ছে|বাঁচতে ইচ্ছে করছে না|आत्महत्या", sig):
            dept, qual, spec = "Psychiatry", "MD", "MD Psychiatry / Emergency Crisis Care"
            reason = _pick_local(
                lang,
                "Acute self-harm or severe mental-health crisis needs immediate crisis helpline (14416 / 112) and emergency psychiatric evaluation.",
                "তীব্র মানসিক সংকট বা আত্মহত্যার ঝুঁকিতে অবিলম্বে টেলি-মানস (১৪৪১৬) / ১১২ নম্বরে যোগাযোগ এবং জরুরি মনোরোগ বিশেষজ্ঞের মূল্যায়ন প্রয়োজন।",
                "गंभीर मानसिक संकट में तुरंत टेली-मानस (14416) / 112 से संपर्क और आपातकालीन मनोरोग मूल्यांकन आवश्यक है।",
            )
        else:
            dept, qual, spec = "Emergency Medicine", "MD", "Emergency Medicine / Casualty (108 / 112)"
            reason = _pick_local(
                lang,
                "Warning signs suggest a potentially life-threatening emergency requiring immediate Emergency Department care.",
                "উপসর্গগুলো সম্ভাব্য প্রাণঘাতী জরুরি অবস্থার ইঙ্গিত দিচ্ছে; অবিলম্বে জরুরি বিভাগে (Emergency) যাওয়া প্রয়োজন।",
                "लक्षण संभावित जानलेवा आपातस्थिति का संकेत देते हैं; तुरंत इमरजेंसी विभाग में जाएं।",
            )
        urg = URGENCY_LEVELS[1]
        return RoutingDecision(
            primary_department=dept,
            primary_department_local=_BN_DEPT.get(dept, dept) if lang == "bn" else dept,
            primary_reason=reason,
            secondary_departments=["Cardiology", "Neurology"] if "chest" in sig or "stroke" in sig else [],
            qualification=qual,
            specialty_title=spec,
            routing_reason=reason,
            initial_vs_specialist="emergency_care",
            urgency_level=1,
            urgency_code=urg["code"],
            urgency_api=urg["api_urgency"],
            urgency_advice=urg.get(lang, urg["en"]),
            legacy_specialist=_DEPT_TO_LEGACY_SPECIALIST.get(dept, "Emergency Department"),
            matched_factors=factors + ["emergency_red_flag"],
        )

    # 2. Clinical Domain Classification (combination of symptoms, duration, comorbidities, age/pregnancy)
    dept = "General Medicine"
    sec: list[str] = []
    qual = "MBBS"
    spec = "MBBS Primary-Care Physician"
    init_mode = "initial_mbbs_assessment"
    urg_lvl = 4 if not subacute else 3

    # Medication adverse reaction / Post-operative complications
    if not is_pregnant and not is_child and re.search(r"\b(side effect|drug reaction|after taking (?:a )?(?:new )?medicine|adverse reaction)\b|ওষুধের পার্শ্বপ্রতিক্রিয়া|दवा के बाद|रिएक्शन", sig):
        dept, sec = "Internal Medicine", ["General Medicine"]
        qual, spec = "MD", "MD General Medicine"
        init_mode, urg_lvl = "direct_specialist_evaluation", 2 if is_severe else 3
        factors.append("medication_adverse_effect_pattern")
    # Obstetrics / Gynaecology
    elif is_pregnant or re.search(r"\b(periods?|menstrua\w*|pcos|vaginal|pelvic pain|missed period|uterus|ovary)\b|মাসিক|পিরিয়ড|সাদা স্রাব|महावारी|माहवारी", sig):
        dept = "Obstetrics" if is_pregnant else "Gynaecology"
        sec = ["General Medicine"]
        qual, spec = "MD", "MD/MS Obstetrics & Gynaecology"
        init_mode = "direct_specialist_evaluation" if is_pregnant else "initial_mbbs_assessment"
        urg_lvl = 2 if (is_pregnant and (is_severe or "pain" in sig or "bleed" in sig or "fever" in sig)) else 3
        factors.append("obstetric_gynae_pattern")

    # Paediatrics
    elif is_child:
        dept = "Paediatrics"
        qual = "MD" if (subacute or is_severe or "fever" in sig) else "MBBS"
        spec = "MD Paediatrics" if qual == "MD" else "MBBS Doctor (or MD Paediatrics)"
        init_mode = "direct_specialist_evaluation" if qual == "MD" else "initial_mbbs_assessment"
        urg_lvl = 2 if (subacute or is_severe or "fever" in sig) else 3
        factors.append("paediatric_care")

    # Cardiology (chest discomfort, pain, tightness, palpitations, exertional breathlessness, high BP, known heart disease)
    elif re.search(r"\b(chest pain|chest tightness|chest discomfort|chest pressure|chest heaviness|palpitations?|angina|heart|cardiac|irregular heartbeat|high blood pressure|hypertension|bp\s*\d{2,3}/\d{2,3}|buk(?:e)? dhorfor)\b|বুকে ব্যথা|বুকে চাপ|বুকে অস্বস্তি|বুক ধড়ফড়|বুক ধড়ফড়|প্রেসার\s*[০-৯0-9]+|রক্তচাপ|सीने में दर्द|सीने में दबाव|धड़कन|बीपी\s*\d{2,3}/\d{2,3}", sig):
        dept = "Cardiology"
        sec = ["Internal Medicine", "Emergency Medicine"]
        if re.search(r"heart failure|cardiomyopathy|arrhythmia|angioplasty|stent|valvular|post[- ]mi", sig):
            qual, spec = "DM", "DM Cardiology"
            init_mode = "direct_specialist_evaluation"
        else:
            qual, spec = "MD / DM", "MD General Medicine / DM Cardiology"
            init_mode = "direct_specialist_evaluation"
        urg_lvl = 2
        factors.append("cardiovascular_pattern")

    # Neurology / Neurosurgery (persistent/severe headache, vertigo, numbness, tingling, seizures, limb weakness)
    elif re.search(r"\b(headache|migraine|vertigo|dizz\w*|numbness|tingling|seizure|weakness in (?:arm|leg)|neuropath|tremor|matha\s+(?:\w+\s+){0,3}(?:betha|byatha|ghurche))\b|মাথাব্যথা|মাথা ঘোর|ঝিনঝিন|অবশ|স্নায়ু|सिरदर्द|चक्कर|झुनझुनी", sig):
        if re.search(r"brain tumour|tumor|aneurysm|spinal cord compression|head injury|subdural|hydrocephalus", sig):
            dept, qual, spec = "Neurosurgery", "MCh", "MCh Neurosurgery"
            init_mode, urg_lvl = "direct_specialist_evaluation", 2
        elif chronic or is_severe or re.search(r"numbness|tingling|seizure|migraine|vertigo|ঝিনঝিন|অবশ|করवट|চक्कर|चक्कर|মাথা ঘোর", sig):
            dept = "Neurology"
            sec = ["General Medicine", "ENT"] if re.search(r"vertigo|dizz|মাথা ঘোর", sig) else ["General Medicine"]
            qual = "DM" if re.search(r"seizure|epilepsy|stroke|parkinson|multiple sclerosis|dementia", sig) else "MD"
            spec = f"{qual} Neurology"
            init_mode = "direct_specialist_evaluation" if (chronic or is_severe) else "initial_mbbs_assessment"
            urg_lvl = 2 if is_severe else 3
        else:
            dept, qual, spec = "General Medicine", "MBBS", "MBBS Primary-Care Physician (MD Medicine / Neurology if persistent)"
            init_mode, urg_lvl = "initial_mbbs_assessment", 3 if subacute else 4
        factors.append("neurological_pattern")

    # Urology / Nephrology (placed BEFORE Orthopaedics so ankle/leg swelling with kidney signs routes to Nephrology)
    elif re.search(r"\b(urinat\w*|dysuria|uti|kidney|renal|creatinine|flank pain|prostate|blood in urine|frothy urine|proteinuria)\b|প্রস্রাব|কিডনি|पेशाब|किडनी|गुर्दे|क्रिएटिनिन", sig):
        if re.search(r"ckd|chronic kidney|dialysis|nephrotic|high creatinine|creatinine|proteinuria|frothy urine|kidney problem|renal|কিডনি|প্রস্রাবে ফেনা|झाग|क्रिएटिनिन|किडनी|गुर्दे", sig):
            dept, sec = "Nephrology", ["Internal Medicine"]
            qual, spec = ("DM", "DM Nephrology") if re.search(r"ckd|dialysis|nephrotic|creatinine", sig) else ("MD / DM", "MD General Medicine / DM Nephrology")
            init_mode, urg_lvl = "direct_specialist_evaluation", 2 if is_severe else 3
        elif re.search(r"kidney stone|renal colic|prostate|blood in urine|stricture|পাথর", sig):
            dept, sec = "Urology", ["Nephrology"]
            qual, spec = "MCh", "MCh Urology (or MS General Surgery)"
            init_mode, urg_lvl = "direct_specialist_evaluation", 2 if is_severe else 3
        else:
            dept, sec = "Urology", ["General Medicine"]
            qual, spec = "MBBS", "MBBS Primary-Care Physician (or Urology if recurrent)"
            init_mode, urg_lvl = "initial_mbbs_assessment", 3
        factors.append("renal_urological_pattern")

    # Orthopaedics / Rheumatology / PMR (knee pain, back pain, joint pain, swelling)
    elif re.search(r"\b(knee|joints?|back pain|spine|shoulder|ankle|fracture|sprain|arthritis|sciatica)\b|হাঁটু|কোমর|পিঠ|জয়েন্ট|হাড়|ঘুটনে|घुटने|कमर|जोड़ों|जोड़|अकड़न", sig):
        multi_joint_autoimmune = bool(re.search(r"morning stiffness|multiple joints|rheumatoid|lupus|autoimmune", sig))
        if multi_joint_autoimmune:
            dept, sec = "Rheumatology", ["Orthopaedics", "Internal Medicine"]
            qual, spec = "DM", "DM Rheumatology (or MD Internal Medicine)"
            init_mode, urg_lvl = "direct_specialist_evaluation", 3
        elif chronic or is_severe or re.search(r"swelling|locked|injury|fall|radiating|numbness|ফোলা|চোট", sig):
            dept, sec = "Orthopaedics", ["Physical Medicine and Rehabilitation"]
            qual, spec = "MS", "MS Orthopaedics"
            init_mode, urg_lvl = "direct_specialist_evaluation", 3
        else:
            dept, sec = "Orthopaedics", ["General Medicine"]
            qual, spec = "MBBS", "MBBS Primary-Care Doctor (with MS Orthopaedics referral if persistent)"
            init_mode, urg_lvl = "initial_mbbs_assessment", 4
        factors.append("musculoskeletal_pattern")

    # Pulmonology (cough + breathlessness / wheezing / prolonged cough)
    elif re.search(r"\b(cough|wheez|asthma|copd|breathless|shortness of breath|sputum|tb|tuberculosis)\b|কাশি|শ্বাসকষ্ট|হাঁপানি|खांसी|सांस", sig):
        dept = "Pulmonology"
        sec = ["General Medicine"]
        if chronic or re.search(r"asthma|copd|wheez|breathless|শ্বাসকষ্ট|tb", sig):
            qual, spec = "MD", "MD Respiratory Medicine (Pulmonology)"
            init_mode = "direct_specialist_evaluation"
            urg_lvl = 2 if re.search(r"breathless|শ্বাসকষ্ট|blood", sig) else 3
        else:
            qual, spec = "MBBS", "MBBS Primary-Care Physician (MD Respiratory Medicine if >2 weeks)"
            init_mode, urg_lvl = "initial_mbbs_assessment", 3 if subacute else 4
        factors.append("respiratory_pattern")

    # Gastroenterology / Hepatology / General Surgery (abdominal pain, vomiting, diarrhoea, jaundice, reflux)
    elif re.search(r"\b(abdom|stomach|belly|diarrh|vomit|nausea|acidity|ulcer|jaundice|liver|gallstone|appendix|hernia|pete ?byatha|pet ?byatha|pet (?:mein )?dard|ulti)\b|পেটে ব্যথা|বমি|পায়খানা|জন্ডিস|অ্যাসিডিটি|লিভার|पेट दर्द|पेट में|उल्टी|एसिडिटी", sig):
        if re.search(r"jaundice|yellow eyes|cirrhosis|hepatitis|fatty liver|ascites|জন্ডিস|লিভার", sig):
            dept, sec = "Hepatology", ["Gastroenterology", "Internal Medicine"]
            qual, spec = "DM", "DM Hepatology / DM Gastroenterology"
            init_mode, urg_lvl = "direct_specialist_evaluation", 2
        elif re.search(r"hernia|appendic|gallstone|perianal|fissure|piles", sig):
            dept, sec = "General Surgery", ["Gastroenterology"]
            qual, spec = "MS", "MS General Surgery"
            init_mode, urg_lvl = "direct_specialist_evaluation", 2 if is_severe else 3
        elif chronic or is_severe or re.search(r"black stool|blood in stool|persistent vomiting", sig):
            dept, sec = "Gastroenterology", ["Internal Medicine"]
            qual, spec = "MD", "MD / DNB Gastroenterology (or DM Gastroenterology if complex)"
            init_mode, urg_lvl = "direct_specialist_evaluation", 2 if is_severe else 3
        else:
            dept, sec = "Gastroenterology", ["General Medicine"]
            qual, spec = "MBBS", "MBBS Primary-Care Physician (or MD/DNB Gastroenterology if persistent)"
            init_mode, urg_lvl = "initial_mbbs_assessment", 3 if subacute else 4
        factors.append("gi_hepatic_pattern")

    # Primary Endocrine Report / Thyroid / Sugar presentation (takes precedence over secondary comorbidities)
    elif re.search(r"\b(blood sugar|hba1c|insulin|thyroid|tsh|hypothyroid|hyperthyroid|pcos)\b|থাইরয়েড|थायरॉइड", sig):
        dept, sec = "Endocrinology", ["Internal Medicine"]
        if re.search(r"uncontrolled|hypoglycaemia|ketoacidosis|complicated|thyroid storm|nodule|tsh|hba1c", sig) or chronic:
            qual, spec = "DM", "MD General Medicine or DM Endocrinology"
            init_mode, urg_lvl = "direct_specialist_evaluation", 3
        else:
            qual, spec = "MD", "MD Internal Medicine / Endocrinology"
            init_mode, urg_lvl = "initial_mbbs_assessment", 3
        factors.append("endocrine_metabolic_pattern")

    # Dermatology
    elif re.search(r"\b(rash|itch|eczema|psoriasis|fungal|ringworm|hives|skin allergy|acne|urticaria)\b|র‍্যাশ|চুলকানি|ত্বক|ফুসকুড়ি|অ্যালার্জি|खुजली|चकत्ते|दाने", sig):
        dept, sec = "Dermatology", ["General Medicine"]
        qual = "MD" if (chronic or is_severe) else "MBBS"
        spec = "MD Dermatology" if qual == "MD" else "MBBS Doctor (MD Dermatology if persistent)"
        init_mode = "direct_specialist_evaluation" if qual == "MD" else "initial_mbbs_assessment"
        urg_lvl = 3 if chronic else 4
        factors.append("dermatology_pattern")

    # Psychiatry / Mental Health
    elif re.search(r"\b(anxiety|panic|depress|insomnia|stress|hallucination|bipolar|ocd|mental health)\b|দুশ্চিন্তা|মন খারাপ|ঘুম হচ্ছে না|উদ্বেগ|মানসিক|ঘবরা|घबराहट|बेचैन|नींद न आने", sig):
        dept, sec = "Psychiatry", ["General Medicine"]
        qual, spec = "MD", "MD Psychiatry"
        init_mode, urg_lvl = "direct_specialist_evaluation", 3
        factors.append("mental_health_pattern")

    # ENT / Ophthalmology / Oncology / Infectious Disease
    elif re.search(r"\b(ear ?ache|tinnitus|hearing loss|sinus|tonsill|sore throat|hoarse)\b|কানে ব্যথা|গলা ব্যথা|সাইনাস|(?:^|[\s,])कान(?:\s|में|दर्द|$)|गले में|खराश", sig):
        dept, sec = "ENT", ["General Medicine"]
        qual, spec = ("MS", "MS ENT") if chronic else ("MBBS", "MBBS Primary-Care Physician")
        init_mode, urg_lvl = ("direct_specialist_evaluation" if chronic else "initial_mbbs_assessment"), 3 if chronic else 4
        factors.append("ent_pattern")
    elif re.search(r"\b(red eye|eye pain|blurred vision|glaucoma|cataract)\b|চোখ|দৃষ্টি|आंख|आँख|धुंधला", sig):
        dept, sec = "Ophthalmology", ["General Medicine"]
        qual, spec = "MS", "MS Ophthalmology"
        init_mode, urg_lvl = "direct_specialist_evaluation", 2 if is_severe else 3
        factors.append("ophthalmology_pattern")
    elif re.search(r"\b(breast\s+lump|lump\s+in\s+(?:my\s+)?breast|nipple\s+discharge|breast\s+mass)\b|স্তনে\s*চাকা|স্তনে\s*গাঁট|स्तन\s*में\s*गांठ", sig):
        dept, sec = "Oncology", ["General Surgery", "Gynaecology"]
        qual, spec = "MS / MCh", "Surgical Oncologist / Breast Surgeon"
        init_mode, urg_lvl = "direct_specialist_evaluation", 2
        factors.append("breast_oncology_pattern")
    elif re.search(r"\b(blood\s+cancer|leukemia|lymphoma|swollen\s+lymph\s+nodes?|drenching\s+night\s+sweats?|easy\s+bruising)\b|লসিকা\s*গ্রন্থি\s*ফোলা|ব্লাড\s*ক্যান্সার", sig):
        dept, sec = "Haematology", ["Oncology", "Internal Medicine"]
        qual, spec = "DM", "DM Clinical Haematology / Medical Oncologist"
        init_mode, urg_lvl = "direct_specialist_evaluation", 2
        factors.append("haematology_oncology_pattern")
    elif re.search(r"\b(post[- ]?coital\s+bleeding|post[- ]?menopausal\s+bleeding|cervical\s+cancer|abnormal\s+pap)\b|জরায়ু\s*থেকে\s*রক্তপাত", sig):
        dept, sec = "Gynaecology", ["Oncology"]
        qual, spec = "MD / MCh", "Gynaecologic Oncologist / Obstetrician-Gynaecologist"
        init_mode, urg_lvl = "direct_specialist_evaluation", 2
        factors.append("gynaecologic_oncology_pattern")
    elif re.search(r"\b(cancer|tumour|tumor|malignan\w*|biopsy|chemo|oncology|painless lump|neck lump|persistent lump|unexplained weight loss)\b|ক্যান্সার|টিউমার|গাঁট|गांठ", sig):
        dept, sec = "Oncology", ["General Surgery", "ENT", "Internal Medicine"]
        qual, spec = "DM", "DM Medical Oncology (or MS Surgical Oncology)"
        init_mode, urg_lvl = "direct_specialist_evaluation", 2
        factors.append("oncology_pattern")
    elif re.search(r"\b(after surgery|post[- ]op|surgical wound|stitch|incision)\b|অপারেশনের পর|সেলাই|सर्जरी के बाद|टांके", sig):
        dept, sec = "General Surgery", ["General Medicine"]
        qual, spec = "MS", "MS General Surgery"
        init_mode, urg_lvl = "direct_specialist_evaluation", 2 if (is_severe or "fever" in sig or "pus" in sig) else 3
        factors.append("post_operative_pattern")
    elif re.search(r"\b(toothache|tooth pain|gum swelling|dental|cavity)\b|দাঁতে ব্যথা|মাড়ি|दांत दर्द|मसूड़ों", sig):
        dept, sec = "Dentistry", ["General Medicine"]
        qual, spec = "BDS / MDS", "BDS / MDS Dental Surgeon"
        init_mode, urg_lvl = "direct_specialist_evaluation", 2 if is_severe else 3
        factors.append("dental_pattern")
    elif re.search(r"\b(side effect|drug reaction|after taking medicine|adverse reaction)\b|ওষুধের পার্শ্বপ্রতিক্রিয়া|दवा के बाद|रिएक्शन", sig):
        dept, sec = "Internal Medicine", ["General Medicine"]
        qual, spec = "MD", "MD General Medicine"
        init_mode, urg_lvl = "direct_specialist_evaluation", 2 if is_severe else 3
        factors.append("medication_adverse_effect_pattern")
    elif re.search(r"\b(fever|dengue|malaria|typhoid|infection)\b|জ্বর|बुखार", sig):
        dept = "Infectious Disease" if days >= 5 else "General Medicine"
        sec = ["Internal Medicine"]
        qual = "MD" if days >= 3 else "MBBS"
        spec = "MD General Medicine / Infectious Disease" if qual == "MD" else "MBBS General Physician"
        init_mode = "direct_specialist_evaluation" if days >= 5 else "initial_mbbs_assessment"
        urg_lvl = 2 if subacute else 4
        factors.append("febrile_infectious_pattern")
    elif re.search(r"\b(diabet|metformin|hormone)\b|ডায়াবেটিস|সুগার|চিনি|शुगर", sig):
        dept, sec = "Endocrinology", ["Internal Medicine"]
        qual, spec = ("DM", "MD General Medicine or DM Endocrinology") if chronic else ("MD", "MD Internal Medicine / Endocrinology")
        init_mode, urg_lvl = ("direct_specialist_evaluation" if chronic else "initial_mbbs_assessment"), 3
        factors.append("endocrine_metabolic_pattern")

    if re.search(r"\b(diabet\w*)\b|ডায়াবেটিস|शुगर", sig) and dept != "Endocrinology" and "Endocrinology" not in sec:
        sec.append("Endocrinology")

    urg = URGENCY_LEVELS[urg_lvl]
    primary_reason = _pick_local(
        lang,
        f"Symptom combination, duration ({int(days)}d if known), and clinical context point primarily to {dept}.",
        f"উপসর্গের ধরন, সময়কাল এবং শারীরিক প্রেক্ষাপট অনুযায়ী প্রধানত {_BN_DEPT.get(dept, dept)} বিভাগ সবচেয়ে প্রাসঙ্গিক।",
        f"लक्षणों के संयोजन, अवधि और चिकित्सीय स्थिति के आधार पर मुख्य रूप से {dept} विभाग उपयुक्त है।",
    )
    routing_reason = _pick_local(
        lang,
        (
            f"Recommended {spec} ({qual}): "
            + ("appropriate for initial evaluation, physical examination, and basic tests before any super-specialist referral."
               if qual == "MBBS"
               else "justified by symptom specificity, duration, or clinical risk factors.")
        ),
        (
            f"প্রস্তাবিত চিকিৎসক স্তর: {spec} ({qual}) — "
            + ("প্রাথমিক পরীক্ষা-নিরীক্ষা ও সাধারণ মূল্যায়নের জন্য এমবিবিএস চিকিৎসক দিয়ে শুরু করাই সঠিক।"
               if qual == "MBBS"
               else "উপসর্গের নির্দিষ্টতা, দীর্ঘস্থায়িত্ব বা ঝুঁকির কারণে এই বিশেষজ্ঞের পরামর্শ যুক্তিসঙ্গত।")
        ),
        (
            f"सुझावित योग्यता: {spec} ({qual}) — "
            + ("प्रारंभिक जांच और सामान्य मूल्यांकन के लिए MBBS डॉक्टर से शुरुआत करना उचित है।"
               if qual == "MBBS"
               else "लक्षणों की प्रकृति, अवधि या जोखिम कारकों के कारण विशेषज्ञ परामर्श उचित है।")
        ),
    )

    return RoutingDecision(
        primary_department=dept,
        primary_department_local=_BN_DEPT.get(dept, dept) if lang == "bn" else dept,
        primary_reason=primary_reason,
        secondary_departments=[d for d in sec if d != dept],
        qualification=qual,
        specialty_title=spec,
        routing_reason=routing_reason,
        initial_vs_specialist=init_mode,
        urgency_level=urg_lvl,
        urgency_code=urg["code"],
        urgency_api=urg["api_urgency"],
        urgency_advice=urg.get(lang, urg["en"]),
        legacy_specialist=_DEPT_TO_LEGACY_SPECIALIST.get(dept, "General Physician"),
        matched_factors=factors or ["general_clinical_presentation"],
    )
