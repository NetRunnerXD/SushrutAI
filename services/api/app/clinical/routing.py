"""Phase 2 & 3: Intelligent Medical Category Classification & Indian Specialist Routing.

Classifies complaints across 27+ medical specialties using multi-factor clinical context
(symptom combinations, duration, severity, history, age/sex, and differential hypotheses —
never isolated keywords alone), then routes to the appropriate Indian medical qualification
tier: MBBS (Primary Care), MD/MS/DNB (Postgraduate Specialist), or DM/MCh (Super-Specialist).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.clinical.schema import ClinicalExtractionPayload
from app.memory.context import MemoryContext
from app.safety.clinical_safety import SafetyAssessment

SPECIALTY_CATALOG: tuple[str, ...] = (
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
    "Dentistry",
)

CREDENTIAL_NOTICE = (
    "SushrutAI never invents doctor names, registration numbers, hospital affiliations, or availability. "
    "Verify any specific doctor's credentials through the official NMC Indian Medical Register (IMR) or State Medical Council."
)

_DOCTOR_QUERY_RE = re.compile(
    r"(?i)\b(?:which\s+doctor|doctor\s+name|best\s+doctor|recommend\s+a\s+doctor|hospital\s+name|registration\s+number|verify\s+doctor|nmc|imr)\b"
)


@dataclass(frozen=True)
class SpecialtyProfile:
    specialty: str
    md_ms_title: str
    dm_mch_title: str | None
    primary_cues: tuple[str, ...]
    combo_cues: tuple[tuple[str, ...], ...]  # multi-symptom/history combinations for higher confidence
    dm_mch_cues: tuple[str, ...] = ()        # strict super-specialist escalation triggers
    min_days_for_specialist: float = 0.0


_PROFILES: tuple[SpecialtyProfile, ...] = (
    SpecialtyProfile(
        specialty="Emergency Medicine",
        md_ms_title="MD Emergency Medicine",
        dm_mch_title=None,
        primary_cues=("chest pain", "unconscious", "fainting", "seizure", "severe bleeding", "cannot breathe", "suicidal", "stroke", "paralysis"),
        combo_cues=(("chest pain", "sweating"), ("shortness of breath", "blue lips"), ("sudden weakness", "slurred speech")),
    ),
    SpecialtyProfile(
        specialty="Cardiology",
        md_ms_title="MD General Medicine / Cardiology Evaluation",
        dm_mch_title="DM Cardiology",
        primary_cues=("chest pain", "palpitation", "angina", "heart", "exertional breathlessness", "swollen ankles", "syncope", "বুক ধড়ফড়", "सीने में दर्द"),
        combo_cues=(("chest pain", "sweating"), ("palpitation", "fainting"), ("bp", "breathlessness")),
        dm_mch_cues=("heart failure", "arrhythmia", "angina", "coronary", "stent", "pacemaker", "valve", "cardiomyopathy", "recurrent fainting"),
        min_days_for_specialist=2.0,
    ),
    SpecialtyProfile(
        specialty="Neurosurgery",
        md_ms_title="MS General Surgery / Neurological Evaluation",
        dm_mch_title="MCh Neurosurgery",
        primary_cues=("brain tumor", "intracranial", "subdural", "aneurysm", "spinal cord compression", "severe head injury", "hydrocephalus"),
        combo_cues=(("head injury", "vomiting"), ("progressive weakness", "back pain", "urine retention")),
        dm_mch_cues=("brain tumor", "aneurysm", "spinal cord compression", "intracranial", "disc prolapse with deficit", "subdural"),
    ),
    SpecialtyProfile(
        specialty="Neurology",
        md_ms_title="MD Neurology",
        dm_mch_title="DM Neurology",
        primary_cues=("seizure", "convulsion", "numbness", "tingling", "migraine", "tremor", "stroke", "slurred speech", "vertigo", "memory loss", "মাথা ঘোর", "झुनझुनी"),
        combo_cues=(("headache", "blurry vision"), ("numbness", "weakness"), ("dizziness", "turning head")),
        dm_mch_cues=("epilepsy", "refractory seizure", "parkinson", "multiple sclerosis", "myasthenia", "neuropathy", "stroke"),
        min_days_for_specialist=5.0,
    ),
    SpecialtyProfile(
        specialty="Pulmonology",
        md_ms_title="MD Respiratory Medicine",
        dm_mch_title="DM Pulmonary Medicine",
        primary_cues=("wheezing", "asthma", "copd", "shortness of breath", "chronic cough", "coughing blood", "pleuritic", "সাঁস", "दमा"),
        combo_cues=(("cough", "fever", "night sweats"), ("breathlessness", "wheezing")),
        dm_mch_cues=("interstitial lung", "pulmonary fibrosis", "severe copd", "pleural effusion", "haemoptysis"),
        min_days_for_specialist=7.0,
    ),
    SpecialtyProfile(
        specialty="Hepatology",
        md_ms_title="MD General Medicine (Liver Evaluation)",
        dm_mch_title="DM Hepatology",
        primary_cues=("jaundice", "yellow eyes", "cirrhosis", "ascites", "hepatitis", "fatty liver", "জন্ডিস", "पीलिया"),
        combo_cues=(("jaundice", "abdominal swelling"), ("jaundice", "dark urine")),
        dm_mch_cues=("cirrhosis", "ascites", "hepatic encephalopathy", "chronic hepatitis", "portal hypertension"),
    ),
    SpecialtyProfile(
        specialty="Gastroenterology",
        md_ms_title="MD General Medicine (GI Evaluation)",
        dm_mch_title="DM Gastroenterology",
        primary_cues=("abdominal pain", "vomiting", "diarrhea", "acidity", "reflux", "constipation", "blood in stool", "black stool", "dysphagia", "পেট ব্যথা", "पेट दर्द"),
        combo_cues=(("abdominal pain", "vomiting"), ("weight loss", "blood in stool")),
        dm_mch_cues=("crohn", "ulcerative colitis", "ibd", "gi bleed", "chronic pancreatitis", "celiac", "dysphagia"),
        min_days_for_specialist=7.0,
    ),
    SpecialtyProfile(
        specialty="Nephrology",
        md_ms_title="MD General Medicine (Renal Evaluation)",
        dm_mch_title="DM Nephrology",
        primary_cues=("kidney", "creatinine", "reduced urine", "foamy urine", "facial puffiness", "dialysis", "ckd"),
        combo_cues=(("diabetes", "swelling", "urine"), ("hypertension", "creatinine")),
        dm_mch_cues=("ckd", "chronic kidney", "dialysis", "nephrotic", "glomerulonephritis", "rising creatinine"),
    ),
    SpecialtyProfile(
        specialty="Urology",
        md_ms_title="MS General Surgery (Urological Evaluation)",
        dm_mch_title="MCh Urology",
        primary_cues=("kidney stone", "flank pain", "blood in urine", "urine retention", "prostate", "difficulty urinating", "testicular pain"),
        combo_cues=(("flank pain", "burning urination"), ("blood in urine", "pain")),
        dm_mch_cues=("prostate", "ureteric", "stricture", "bladder tumor", "recurrent stone", "renal calculus"),
    ),
    SpecialtyProfile(
        specialty="Endocrinology",
        md_ms_title="MD General Medicine (Endocrine Evaluation)",
        dm_mch_title="DM Endocrinology",
        primary_cues=("diabetes", "thyroid", "hba1c", "hypoglycaemia", "insulin", "pcos", "hormone", "goitre", "সুগার", "थायराइड"),
        combo_cues=(("diabetes", "tingling"), ("weight gain", "fatigue", "thyroid"), ("sweating", "shakiness", "diabetes")),
        dm_mch_cues=("brittle diabetes", "insulin pump", "pituitary", "adrenal", "thyroid storm", "uncontrolled hba1c"),
        min_days_for_specialist=7.0,
    ),
    SpecialtyProfile(
        specialty="Orthopaedics",
        md_ms_title="MS Orthopaedics",
        dm_mch_title=None,
        primary_cues=("fracture", "joint pain", "knee pain", "back pain", "sprain", "dislocation", "neck stiffness", "bone pain", "হাঁটু ব্যথা", "जोड़ों में दर्द"),
        combo_cues=(("fall", "swelling", "cannot walk"), ("back pain", "leg numbness")),
        min_days_for_specialist=3.0,
    ),
    SpecialtyProfile(
        specialty="Physical Medicine and Rehabilitation",
        md_ms_title="MD Physical Medicine and Rehabilitation",
        dm_mch_title=None,
        primary_cues=("rehab", "physiotherapy", "post stroke recovery", "chronic mobility", "stiffness after fracture", "spasticity"),
        combo_cues=(("stroke", "walking difficulty"), ("chronic back pain", "posture")),
        min_days_for_specialist=14.0,
    ),
    SpecialtyProfile(
        specialty="General Surgery",
        md_ms_title="MS General Surgery",
        dm_mch_title=None,
        primary_cues=("hernia", "appendix", "appendicitis", "abscess", "ulcer", "lump", "gallstone", "piles", "fistula", "diabetic foot"),
        combo_cues=(("right lower abdominal pain", "fever"), ("swelling", "pus")),
    ),
    SpecialtyProfile(
        specialty="Dermatology",
        md_ms_title="MD Dermatology",
        dm_mch_title=None,
        primary_cues=("rash", "itching", "eczema", "psoriasis", "acne", "fungal", "hives", "skin lesion", "hair loss", "চুলকানি", "खुजली"),
        combo_cues=(("rash", "blisters"), ("itching", "scaling")),
        min_days_for_specialist=5.0,
    ),
    SpecialtyProfile(
        specialty="ENT",
        md_ms_title="MS ENT (Otorhinolaryngology)",
        dm_mch_title=None,
        primary_cues=("ear pain", "hearing loss", "tinnitus", "sinus", "tonsils", "hoarseness", "nosebleed", "ear discharge", "কানে ব্যথা", "कान दर्द"),
        combo_cues=(("ear fullness", "ringing"), ("sore throat", "difficulty swallowing")),
        min_days_for_specialist=5.0,
    ),
    SpecialtyProfile(
        specialty="Ophthalmology",
        md_ms_title="MS Ophthalmology",
        dm_mch_title=None,
        primary_cues=("blurry vision", "eye pain", "red eye", "vision loss", "floaters", "cataract", "glaucoma", "চোখে ব্যথা", "आँख में दर्द"),
        combo_cues=(("eye pain", "blurry vision"), ("diabetes", "blurry vision")),
    ),
    SpecialtyProfile(
        specialty="Obstetrics",
        md_ms_title="MD / MS Obstetrics and Gynaecology",
        dm_mch_title=None,
        primary_cues=("pregnant", "pregnancy", "trimester", "prenatal", "labour", "fetal movement", "গর্ভবতী", "गर्भवती"),
        combo_cues=(("pregnant", "bleeding"), ("pregnant", "swelling", "headache")),
    ),
    SpecialtyProfile(
        specialty="Gynaecology",
        md_ms_title="MD / MS Obstetrics and Gynaecology",
        dm_mch_title=None,
        primary_cues=("menstrual", "heavy periods", "missed period", "pelvic pain", "vaginal discharge", "menopause", "ovarian"),
        combo_cues=(("heavy periods", "weakness"), ("pelvic pain", "discharge")),
        min_days_for_specialist=3.0,
    ),
    SpecialtyProfile(
        specialty="Paediatrics",
        md_ms_title="MD Paediatrics",
        dm_mch_title=None,
        primary_cues=("child", "infant", "baby", "toddler", "newborn", "paediatric", "শিশু", "बच्चा"),
        combo_cues=(("child", "fever"), ("baby", "poor feeding")),
    ),
    SpecialtyProfile(
        specialty="Psychiatry",
        md_ms_title="MD Psychiatry",
        dm_mch_title=None,
        primary_cues=("anxiety", "depression", "panic", "insomnia", "hallucination", "suicidal", "self harm", "obsessive", "ঘুম হচ্ছে না", "चिंता"),
        combo_cues=(("low mood", "insomnia"), ("panic", "palpitation")),
        min_days_for_specialist=7.0,
    ),
    SpecialtyProfile(
        specialty="Oncology",
        md_ms_title="MD Radiotherapy / Oncology Evaluation",
        dm_mch_title="DM Medical Oncology",
        primary_cues=("cancer", "chemotherapy", "malignancy", "biopsy confirmed", "tumor", "lymphoma", "carcinoma"),
        combo_cues=(("unexplained weight loss", "persistent lump", "night sweats"),),
        dm_mch_cues=("cancer", "chemotherapy", "malignancy", "metastatic", "carcinoma", "lymphoma"),
    ),
    SpecialtyProfile(
        specialty="Rheumatology",
        md_ms_title="MD General Medicine (Autoimmune Evaluation)",
        dm_mch_title="DM Clinical Immunology and Rheumatology",
        primary_cues=("morning stiffness", "multiple joint swelling", "lupus", "rheumatoid", "autoimmune", "vasculitis", "gout"),
        combo_cues=(("symmetric joint pain", "morning stiffness"), ("rash", "joint pain", "fever")),
        dm_mch_cues=("lupus", "sle", "vasculitis", "rheumatoid", "ankylosing spondylitis"),
        min_days_for_specialist=14.0,
    ),
    SpecialtyProfile(
        specialty="Infectious Disease",
        md_ms_title="MD General Medicine (Infectious Disease)",
        dm_mch_title="DM Infectious Disease",
        primary_cues=("dengue", "malaria", "typhoid", "tuberculosis", "tb", "hiv", "leptospirosis", "prolonged fever", "ডেঙ্গু", "मलेरिया"),
        combo_cues=(("high fever", "body ache", "rash"), ("fever", "weight loss", "cough")),
        dm_mch_cues=("drug resistant tb", "hiv", "opportunistic infection", "fever of unknown origin"),
        min_days_for_specialist=5.0,
    ),
    SpecialtyProfile(
        specialty="Haematology",
        md_ms_title="MD General Medicine (Haematology Evaluation)",
        dm_mch_title="DM Clinical Haematology",
        primary_cues=("anaemia", "anemia", "low platelets", "thrombocytopenia", "bleeding gums", "thalassemia", "sickle cell", "leukemia"),
        combo_cues=(("pale", "fatigue", "breathless"), ("fever", "petechiae", "bleeding")),
        dm_mch_cues=("thalassemia", "sickle cell", "leukemia", "aplastic", "coagulopathy", "pancytopenia"),
        min_days_for_specialist=7.0,
    ),
    SpecialtyProfile(
        specialty="Internal Medicine",
        md_ms_title="MD Internal Medicine / General Medicine",
        dm_mch_title=None,
        primary_cues=("multisystem", "multiple chronic", "unexplained weight loss", "persistent fatigue", "complex fever"),
        combo_cues=(("diabetes", "hypertension", "weakness"),),
        min_days_for_specialist=7.0,
    ),
    SpecialtyProfile(
        specialty="Dentistry",
        md_ms_title="MDS / BDS Dental Surgeon",
        dm_mch_title=None,
        primary_cues=("toothache", "gum swelling", "wisdom tooth", "dental", "jaw pain", "দাঁত ব্যথা", "दांत दर्द"),
        combo_cues=(("toothache", "facial swelling"),),
    ),
)

_PROFILE_BY_NAME = {p.specialty: p for p in _PROFILES}


def _extract_max_days(extraction: ClinicalExtractionPayload, mem: MemoryContext, text: str) -> float:
    max_days = 0.0
    for s in extraction.symptoms:
        days = getattr(getattr(s, "temporal", None), "duration_days", None)
        if days:
            max_days = max(max_days, float(days))
    for s in mem.short_term.get("active_symptoms", []):
        dtxt = str(s.get("duration") or "")
        if m := re.search(r"(\d+(?:\.\d+)?)\s*(day|week|month|year)", dtxt, re.I):
            val = float(m.group(1))
            u = m.group(2).lower()
            mult = 365 if u.startswith("y") else 30 if u.startswith("m") else 7 if u.startswith("w") else 1
            max_days = max(max_days, val * mult)
    if m := re.search(r"(\d+(?:\.\d+)?)\s*(day|days|week|weeks|month|months|দিন|সপ্তাহ|মাস|दिन|हफ़्ते|महीने)", text, re.I):
        val = float(m.group(1))
        u = m.group(2).lower()
        mult = 30 if u in ("month", "months", "মাস", "महीने") else 7 if u in ("week", "weeks", "সপ্তাহ", "हफ़्ते") else 1
        max_days = max(max_days, val * mult)
    return max_days


class MedicalCategoryClassifier:
    """Phase 2: Multi-factor specialty classifier (symptom combos, duration, history, severity, differentials)."""

    def classify(
        self,
        *,
        user_text: str,
        extraction: ClinicalExtractionPayload,
        mem: MemoryContext,
        safety: SafetyAssessment,
        differentials: list[dict] | None = None,
    ) -> dict:
        sym_names = [s.name for s in extraction.symptoms] + mem.active_symptom_names
        triggers = [t for s in extraction.symptoms for t in s.triggers]
        history_conds = [c.get("name", "") for c in mem.long_term.get("conditions", []) if c.get("name")]
        meds = [m.get("name", "") for m in mem.long_term.get("medications", []) if m.get("name")]
        demo = mem.long_term.get("demographics", {})
        age = demo.get("age")
        pregnant = bool(demo.get("pregnant")) or any(w in (user_text or "").lower() for w in ("pregnant", "pregnancy", "trimester", "গর্ভবতী", "गर्भवती"))
        diff_names = [d.get("explanation", "") for d in (differentials or [])]

        signal = " ".join([
            user_text or "",
            " ".join(sym_names),
            " ".join(triggers),
            " ".join(history_conds),
            " ".join(meds),
            " ".join(safety.positive_findings),
            " ".join(diff_names),
        ]).lower()

        max_days = _extract_max_days(extraction, mem, user_text or "")
        scores: list[tuple[int, SpecialtyProfile, list[str]]] = []

        for prof in _PROFILES:
            score = 0
            reasons: list[str] = []

            matched_cues = [c for c in prof.primary_cues if c in signal]
            if matched_cues:
                score += len(matched_cues) * 2
                reasons.append(f"Clinical cues: {', '.join(matched_cues[:3])}")

            for combo in prof.combo_cues:
                if all(part in signal for part in combo):
                    score += 4
                    reasons.append(f"Symptom/history combination: {' + '.join(combo)}")

            if prof.specialty == "Paediatrics" and isinstance(age, (int, float)) and age < 18:
                score += 6
                reasons.append(f"Patient age ({age} yrs)")
            if prof.specialty == "Obstetrics" and pregnant:
                score += 7
                reasons.append("Pregnancy context")
            if prof.specialty == "Emergency Medicine" and safety.escalate_now:
                score += 8
                reasons.append("Emergency red-flag criteria triggered")

            if score > 0:
                if max_days >= 14 and prof.specialty not in ("Emergency Medicine",):
                    score += 1
                scores.append((score, prof, reasons))

        scores.sort(key=lambda x: x[0], reverse=True)

        if not scores:
            return {
                "primary_specialty": "General Medicine",
                "secondary_specialties": [],
                "multidisciplinary": False,
                "max_duration_days": max_days,
                "reasoning_factors": ["Undifferentiated initial complaint without organ-specific red flags"],
            }

        primary = scores[0]
        # Include secondary specialties only when they have meaningful independent evidence (multidisciplinary care)
        secondary: list[str] = []
        base_top = primary[0] - (8 if primary[1].specialty == "Emergency Medicine" and safety.escalate_now else 0)
        threshold = max(4, base_top // 2)
        for sc, prof, _ in scores[1:4]:
            if prof.specialty != primary[1].specialty and sc >= threshold:
                secondary.append(prof.specialty)

        return {
            "primary_specialty": primary[1].specialty,
            "secondary_specialties": secondary,
            "multidisciplinary": len(secondary) > 0,
            "max_duration_days": max_days,
            "reasoning_factors": primary[2][:4],
            "_signal": signal,
            "_top_score": primary[0],
        }


class SpecialistRoutingEngine:
    """Phase 3: Routes to MBBS (Primary Care), MD/MS/DNB (Specialist), or DM/MCh (Super-Specialist)."""

    def route(
        self,
        *,
        classification: dict,
        user_text: str,
        safety: SafetyAssessment,
        mem: MemoryContext,
    ) -> dict:
        primary = classification.get("primary_specialty") or "General Medicine"
        secondary = list(classification.get("secondary_specialties") or [])
        max_days = float(classification.get("max_duration_days") or 0.0)
        signal = str(classification.get("_signal") or user_text or "").lower()
        prof = _PROFILE_BY_NAME.get(primary)
        asks_specific_doctor = bool(_DOCTOR_QUERY_RE.search(user_text or ""))

        # 1. Emergency overrides routine tiering
        if safety.escalate_now or primary == "Emergency Medicine":
            return {
                "qualification_tier": "md_ms_dnb" if primary != "General Medicine" else "mbbs",
                "tier_label": "Emergency Care (MBBS / MD Emergency Medicine)",
                "recommended_title": "Emergency Department (MBBS Casualty / MD Emergency Medicine)",
                "primary_specialty": primary,
                "secondary_specialties": secondary,
                "why_appropriate": "Urgent warning signs require immediate in-person emergency evaluation rather than waiting for an outpatient specialist appointment.",
                "escalation_triggers": ["Call 108 / 112 or visit the nearest emergency department immediately."],
                "credential_notice": CREDENTIAL_NOTICE if asks_specific_doctor else None,
            }

        # 2. Check DM / MCh Super-Specialist gate (strictly gated; never for routine symptoms)
        has_dm_trigger = bool(prof and prof.dm_mch_title and any(c in signal for c in prof.dm_mch_cues))
        has_complex_history = len(mem.long_term.get("conditions", [])) >= 2 and max_days >= 14
        if prof and prof.dm_mch_title and (has_dm_trigger or (has_complex_history and prof.specialty in ("Cardiology", "Neurology", "Endocrinology", "Nephrology"))):
            return {
                "qualification_tier": "dm_mch",
                "tier_label": "DM / MCh Super-Specialist",
                "recommended_title": prof.dm_mch_title,
                "primary_specialty": primary,
                "secondary_specialties": secondary,
                "why_appropriate": (
                    f"Advanced subspecialty features or complex chronic involvement point toward {prof.dm_mch_title} "
                    f"(typically following or alongside initial medical evaluation)."
                ),
                "escalation_triggers": ["Seek urgent emergency care if symptoms suddenly worsen before your specialist visit."],
                "credential_notice": CREDENTIAL_NOTICE if asks_specific_doctor else None,
            }

        # 3. Decide between MBBS (Primary Care) and MD / MS / DNB (Postgraduate Specialist)
        # Mild, short-duration, or undifferentiated complaints belong at MBBS primary care first;
        # clear organ-specific or persistent/urgent complaints route to the matching MD / MS / DNB specialist.
        top_score = classification.get("_top_score", 0)
        is_mild_acute = (0 < max_days <= 2) and top_score < 4 and safety.severity not in ("urgent", "see_doctor_24h", "emergency")
        needs_specialist = False
        if prof is not None:
            if prof.specialty in ("Paediatrics", "Obstetrics", "Ophthalmology", "General Surgery", "Dentistry", "Gynaecology"):
                needs_specialist = True
            elif prof.specialty not in ("General Medicine", "Internal Medicine"):
                if safety.severity in ("urgent", "see_doctor_24h", "moderate") or top_score >= 4 or (max_days == 0 and top_score >= 2):
                    needs_specialist = True
                elif max_days >= prof.min_days_for_specialist and not is_mild_acute:
                    needs_specialist = True
            elif max_days >= prof.min_days_for_specialist and (top_score >= 4 or max_days >= 7.0):
                needs_specialist = True

        if needs_specialist and prof is not None:
            return {
                "qualification_tier": "md_ms_dnb",
                "tier_label": "MD / MS / DNB Specialist",
                "recommended_title": prof.md_ms_title,
                "primary_specialty": primary,
                "secondary_specialties": secondary,
                "why_appropriate": (
                    f"Symptom pattern, duration, and clinical context align with specialist evaluation by {prof.md_ms_title}."
                    + (f" Multidisciplinary input from {', '.join(secondary)} may also help." if secondary else "")
                ),
                "escalation_triggers": [
                    f"Further referral to {prof.dm_mch_title} may be considered if symptoms persist or specialized procedures are needed."
                    if prof.dm_mch_title else "Escalate to emergency care if red-flag symptoms develop."
                ],
                "credential_notice": CREDENTIAL_NOTICE if asks_specific_doctor else None,
            }

        # 4. Default: MBBS Primary Care Physician (avoid over-escalation)
        return {
            "qualification_tier": "mbbs",
            "tier_label": "MBBS Primary Care",
            "recommended_title": "MBBS General Physician (Primary Care)",
            "primary_specialty": primary,
            "secondary_specialties": secondary,
            "why_appropriate": (
                "An MBBS-qualified registered medical practitioner is the right first step for initial physical examination, "
                "basic evaluation, and deciding if a specialist referral is needed — avoiding unnecessary specialist delays."
            ),
            "escalation_triggers": [
                f"If symptoms persist beyond a few days or worsen, your MBBS doctor can refer you to {prof.md_ms_title if prof else 'the appropriate MD/MS specialist'}."
            ],
            "credential_notice": CREDENTIAL_NOTICE if asks_specific_doctor else None,
        }
