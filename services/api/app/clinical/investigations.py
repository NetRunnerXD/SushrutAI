"""Patient-dependent Clinical Investigation Recommendation Engine.

Selects investigations tailored to the patient's symptoms, duration, medications, and safety findings
(NEVER a universal test package), checks existing LabResults already in the patient's chart, and
clearly states purpose (screening | diagnostic | monitoring), urgency, limitations, and clinician review.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.memory.context import MemoryContext
from app.models import ClinicalInvestigation, LabResult
from app.safety.clinical_safety import SafetyAssessment
from app.security.auth import CurrentUser


@dataclass(frozen=True)
class InvestigationTemplate:
    name: str
    aliases: tuple[str, ...]
    purpose_category: str  # screening | diagnostic | monitoring
    why_relevant: str
    trigger_cues: tuple[str, ...]
    min_duration_days: float = 0.0
    urgent_cues: tuple[str, ...] = ()
    limitations: str = "A single test result must be interpreted alongside physical examination by a clinician."
    domain_category: str = "blood"  # blood | urine_stool | imaging | cardiac | neurological | respiratory | specialist_procedure
    what_it_evaluates: str = ""
    stage: str = "initial_consideration"  # initial_consideration | specialist_later
    radiation_risk: bool = False
    preparation: str = "No special preparation usually needed; confirm with your doctor or lab."


_CATALOGUE: tuple[InvestigationTemplate, ...] = (
    InvestigationTemplate(
        name="Complete Blood Count (CBC)",
        aliases=("cbc", "haemoglobin", "hemoglobin", "hb", "platelets", "wbc"),
        purpose_category="diagnostic",
        why_relevant="Checks for anaemia (low haemoglobin causing weakness/dizziness), infection, or low platelets in prolonged fever.",
        trigger_cues=("weakness", "dizziness", "fever", "head heaviness", "malaise", "pale", "fatigue", "দুর্বল", "জ্বর"),
        min_duration_days=3.0,
        urgent_cues=("bleeding", "petechiae", "fainting"),
    ),
    InvestigationTemplate(
        name="Blood glucose (fasting / random)",
        aliases=("glucose", "blood sugar", "rbs", "fbs", "ppbs", "sugar"),
        purpose_category="screening",
        why_relevant="Evaluates low or high blood sugar as a contributor to dizziness, weakness, sweating, or fatigue.",
        trigger_cues=("dizziness", "weakness", "diabetes", "metformin", "glimepiride", "insulin", "tremor", "sweating", "head heaviness"),
        preparation="Fasting blood glucose requires 8–12 hours without food or caloric drinks (plain water is allowed); random glucose needs no fasting.",
    ),
    InvestigationTemplate(
        name="HbA1c",
        aliases=("hba1c", "a1c", "glycated"),
        purpose_category="monitoring",
        why_relevant="Measures 3-month average blood sugar control for existing diabetes or persistent metabolic risk.",
        trigger_cues=("diabetes", "metformin", "glimepiride", "insulin", "high sugar"),
    ),
    InvestigationTemplate(
        name="Serum Electrolytes (Sodium, Potassium)",
        aliases=("electrolytes", "sodium", "potassium", "na+", "k+"),
        purpose_category="diagnostic",
        why_relevant="Checks salt imbalances from poor oral intake, vomiting, diarrhea, heat exposure, or BP medicines.",
        trigger_cues=("vomiting", "diarrhea", "poor oral intake", "dehydration", "bp", "amlodipine", "telmisartan", "furosemide", "severe weakness"),
    ),
    InvestigationTemplate(
        name="Kidney Function Tests (Creatinine / BUN)",
        aliases=("creatinine", "bun", "kft", "rft", "kidney"),
        purpose_category="monitoring",
        why_relevant="Checks kidney filtration before/during chronic medications, dehydration, or high blood pressure.",
        trigger_cues=("kidney", "bp", "hypertension", "swelling", "urine", "diabetes", "telmisartan"),
    ),
    InvestigationTemplate(
        name="Liver Function Tests (LFT)",
        aliases=("lft", "sgpt", "sgot", "alt", "ast", "bilirubin", "liver"),
        purpose_category="diagnostic",
        why_relevant="Helpful if there is nausea, yellowing of eyes (jaundice), upper abdominal pain, or prolonged fever.",
        trigger_cues=("jaundice", "yellow", "right upper abdomen", "nausea", "vomiting", "hepatitis", "alcohol"),
    ),
    InvestigationTemplate(
        name="Thyroid Function (TSH)",
        aliases=("tsh", "thyroid", "t3", "t4"),
        purpose_category="screening",
        why_relevant="Assesses thyroid imbalance when unexplained chronic fatigue, palpitations, or weight changes are present.",
        trigger_cues=("thyroid", "palpitation", "fatigue", "weeks", "weight"),
        min_duration_days=14.0,
    ),
    InvestigationTemplate(
        name="Iron Studies / Ferritin",
        aliases=("ferritin", "iron", "tibc"),
        purpose_category="diagnostic",
        why_relevant="Identifies iron depletion when weakness/dizziness accompanies anaemia or heavy blood loss.",
        trigger_cues=("anaemia", "anemia", "low haemoglobin", "heavy periods", "persistent weakness"),
        min_duration_days=5.0,
    ),
    InvestigationTemplate(
        name="Vitamin B12",
        aliases=("b12", "vitamin b12", "cobalamin"),
        purpose_category="diagnostic",
        why_relevant="Checks B12 deficiency when weakness, numbness/tingling, or vegetarian diet history is present.",
        trigger_cues=("tingling", "numbness", "veg", "vegan", "metformin", "persistent fatigue"),
        min_duration_days=7.0,
    ),
    InvestigationTemplate(
        name="Urinalysis",
        aliases=("urine", "urinalysis", "pus cells"),
        purpose_category="diagnostic",
        why_relevant="Evaluates urinary infection, protein loss, or dehydration when burning urination, frothy urine, or flank pain occurs.",
        trigger_cues=("burning urination", "dysuria", "urinary", "flank pain", "frothy urine", "proteinuria", "প্রস্রাব", "पेशाब"),
    ),
    InvestigationTemplate(
        name="12-lead ECG",
        aliases=("ecg", "ekg", "electrocardiogram"),
        purpose_category="diagnostic",
        why_relevant="Evaluates heart rhythm and ischemia urgently when fainting, chest symptoms, palpitations, or severe unexplained dizziness occur.",
        trigger_cues=("chest pain", "palpitation", "fainting", "shortness of breath", "blackout", "orthostatic", "standing up"),
        urgent_cues=("chest pain", "fainting", "shortness of breath", "palpitation"),
        limitations="A single resting ECG only captures rhythm at that moment; intermittent arrhythmias may need longer monitoring.",
    ),
    InvestigationTemplate(
        name="Holter Monitoring",
        aliases=("holter", "ambulatory ecg"),
        purpose_category="monitoring",
        why_relevant="Tracks intermittent heart rhythm changes if episodic palpitations or unexplained near-fainting recur despite normal resting ECG.",
        trigger_cues=("intermittent palpitation", "recurrent fainting", "episodic blackout"),
        min_duration_days=3.0,
    ),
    InvestigationTemplate(
        name="Echocardiography",
        aliases=("echo", "echocardiogram", "2d echo"),
        purpose_category="diagnostic",
        why_relevant="Ordered by a physician when structural heart disease or exertional syncope/breathlessness is suspected.",
        trigger_cues=("heart failure", "exertional breathlessness", "swollen ankles", "cardiac syncope"),
        domain_category="cardiac",
        what_it_evaluates="Heart chamber size, pumping function, and valve motion.",
        stage="specialist_later",
    ),
    InvestigationTemplate(
        name="Cardiac Biomarkers (Troponin)",
        aliases=("troponin", "cardiac enzymes", "ck-mb"),
        purpose_category="diagnostic",
        why_relevant="Urgent hospital blood test if acute chest pain or suspected heart attack is evaluated in Emergency.",
        trigger_cues=("chest pain", "chest tightness", "angina", "বুকে ব্যথা", "सीने में दर्द"),
        urgent_cues=("chest pain", "chest tightness", "angina", "বুকে ব্যথা", "सीने में दर्द"),
        domain_category="cardiac",
        what_it_evaluates="Heart muscle injury; decided immediately by an emergency physician.",
        stage="specialist_later",
    ),
    InvestigationTemplate(
        name="Lipid Profile",
        aliases=("lipid", "cholesterol", "triglycerides", "ldl", "hdl"),
        purpose_category="screening",
        why_relevant="Evaluates cardiovascular and metabolic risk alongside diabetes, high BP, or chest symptoms.",
        trigger_cues=("cholesterol", "diabetes", "hypertension", "chest pain", "হৃদরোগ"),
        domain_category="blood",
        what_it_evaluates="Cholesterol and triglyceride fractions for long-term heart risk.",
        stage="initial_consideration",
        preparation="Usually requires 9–12 hours of overnight fasting (plain water is fine); confirm with your doctor.",
    ),
    InvestigationTemplate(
        name="ESR / CRP (Inflammatory Markers)",
        aliases=("esr", "crp", "c-reactive protein"),
        purpose_category="diagnostic",
        why_relevant="Helps assess active inflammation when joint swelling, prolonged fever, or autoimmune features are present.",
        trigger_cues=("joint swelling", "multiple joints", "prolonged fever", "rheumatoid", "morning stiffness"),
        min_duration_days=7.0,
        domain_category="blood",
        what_it_evaluates="Non-specific inflammation levels in the body.",
        stage="initial_consideration",
    ),
    InvestigationTemplate(
        name="Urine Culture & Sensitivity",
        aliases=("urine culture", "urine c/s"),
        purpose_category="diagnostic",
        why_relevant="Identifies specific bacteria and effective antibiotics if urinary infection persists or recurs despite initial care.",
        trigger_cues=("recurrent uti", "persistent burning urination", "fever with burning", "pyelonephritis"),
        domain_category="urine_stool",
        what_it_evaluates="Bacterial growth in urine; ordered by a doctor before/during targeted treatment.",
        stage="specialist_later",
    ),
    InvestigationTemplate(
        name="Stool Routine Examination",
        aliases=("stool routine", "stool test", "ova cyst"),
        purpose_category="diagnostic",
        why_relevant="Helpful if persistent watery diarrhea, blood/mucus in stool, or abdominal cramps last several days.",
        trigger_cues=("diarrhea", "loose motion", "blood in stool", "পাতলা পায়খানা", "দস্ত"),
        min_duration_days=3.0,
        domain_category="urine_stool",
        what_it_evaluates="Stool infection, parasites, or occult blood.",
        stage="initial_consideration",
    ),
    InvestigationTemplate(
        name="Chest X-ray",
        aliases=("chest x-ray", "cxr", "chest xray"),
        purpose_category="diagnostic",
        why_relevant="Considered by a clinician if cough persists >2 weeks, or accompanies fever, breathlessness, or abnormal chest sounds.",
        trigger_cues=("prolonged cough", "breathlessness", "shortness of breath", "chest pain", "tb", "pneumonia", "শ্বাসকষ্ট"),
        min_duration_days=7.0,
        domain_category="respiratory",
        what_it_evaluates="Lungs, airways, and heart silhouette.",
        stage="initial_consideration",
        radiation_risk=True,
    ),
    InvestigationTemplate(
        name="Breast Ultrasound / Diagnostic Mammography",
        aliases=("mammography", "mammogram", "breast ultrasound"),
        purpose_category="diagnostic",
        why_relevant="Considered by a breast surgeon or oncologist to evaluate a persistent breast lump or discharge.",
        trigger_cues=("breast lump", "breast mass", "nipple discharge", "স্তনে চাকা", "स्तन में गांठ"),
        domain_category="imaging",
        what_it_evaluates="Breast tissue structure and whether a lump is cystic or solid.",
        stage="specialist_later",
    ),
    InvestigationTemplate(
        name="Tissue Biopsy (Specialist-Decided)",
        aliases=("biopsy", "fnac", "core biopsy"),
        purpose_category="diagnostic",
        why_relevant="Only performed when a specialist identifies a suspicious lump or lesion after physical exam and imaging.",
        trigger_cues=("breast lump", "painless lump", "neck lump", "persistent lump", "tumour", "tumor", "cancer", "ক্যান্সার", "স্তনে চাকা"),
        domain_category="specialist_procedure",
        what_it_evaluates="Cellular pathology to establish or rule out malignancy.",
        stage="specialist_later",
    ),
    InvestigationTemplate(
        name="Pap Smear / HPV & Colposcopy Evaluation",
        aliases=("pap smear", "hpv", "colposcopy"),
        purpose_category="screening",
        why_relevant="Evaluates cervical cell changes when abnormal bleeding or screening concerns arise.",
        trigger_cues=("cervical", "post-coital bleeding", "post-menopausal bleeding", "জরায়ু"),
        domain_category="specialist_procedure",
        what_it_evaluates="Cervical health; decided after gynaecological examination.",
        stage="specialist_later",
    ),
    InvestigationTemplate(
        name="Spirometry (Pulmonary Function Test)",
        aliases=("spirometry", "pft", "pulmonary function"),
        purpose_category="diagnostic",
        why_relevant="Evaluates airflow obstruction when chronic wheezing, asthma, or persistent breathlessness is suspected.",
        trigger_cues=("wheezing", "asthma", "copd", "chronic cough", "হাঁপানি"),
        min_duration_days=14.0,
        domain_category="respiratory",
        what_it_evaluates="Lung volumes and airway narrowing.",
        stage="specialist_later",
    ),
    InvestigationTemplate(
        name="Targeted X-ray (Joint / Spine)",
        aliases=("knee x-ray", "spine x-ray", "x-ray"),
        purpose_category="diagnostic",
        why_relevant="Only considered after physical examination if joint/back pain is chronic (>2 weeks), follows injury, or limits weight-bearing.",
        trigger_cues=("knee pain", "back pain", "joint pain", "fall", "injury", "swelling", "হাঁটু", "কোমর"),
        min_duration_days=14.0,
        domain_category="imaging",
        what_it_evaluates="Bone alignment, joint space narrowing, or fracture.",
        stage="specialist_later",
        radiation_risk=True,
    ),
    InvestigationTemplate(
        name="Ultrasound (Abdomen / Pelvis)",
        aliases=("ultrasound", "usg", "sonography"),
        purpose_category="diagnostic",
        why_relevant="Radiation-free imaging considered when persistent abdominal pain, flank pain, jaundice, or obstetric/pelvic symptoms need evaluation.",
        trigger_cues=("abdominal pain", "stomach pain", "flank pain", "jaundice", "gallstone", "pelvic pain", "pregnant", "পেটে ব্যথা", "পেট ব্যথা", "গর্ভবতী"),
        domain_category="imaging",
        what_it_evaluates="Liver, gallbladder, kidneys, urinary tract, or pelvic organs without radiation.",
        stage="initial_consideration",
        preparation="Upper abdominal ultrasound typically needs 6–8 hours fasting; pelvic ultrasound often needs a full bladder — confirm with the imaging centre.",
    ),
    InvestigationTemplate(
        name="MRI Brain / Spine (Specialist-Directed)",
        aliases=("mri", "mri brain", "mri spine"),
        purpose_category="diagnostic",
        why_relevant="Advanced imaging ordered only by a neurologist/orthopaedic specialist when persistent focal deficits, severe chronic headache, or nerve compression exist.",
        trigger_cues=("numbness", "weakness in arm", "seizure", "radiating back pain", "persistent headache", "ঝিনঝিন"),
        min_duration_days=14.0,
        domain_category="neurological",
        what_it_evaluates="Detailed brain, spinal cord, and nerve-root anatomy.",
        stage="specialist_later",
    ),
    InvestigationTemplate(
        name="EEG / Nerve Conduction Study",
        aliases=("eeg", "ncs", "emg"),
        purpose_category="diagnostic",
        why_relevant="Neurologist-directed functional test when seizures/blackouts (EEG) or peripheral nerve tingling/numbness (NCS) need confirmation.",
        trigger_cues=("seizure", "fits", "tingling", "numbness", "neuropathy", "খিঁচুনি", "ঝিনঝিন"),
        min_duration_days=7.0,
        domain_category="neurological",
        what_it_evaluates="Electrical brain activity (EEG) or peripheral nerve signal speed (NCS).",
        stage="specialist_later",
    ),
    InvestigationTemplate(
        name="Upper GI Endoscopy",
        aliases=("endoscopy", "ugie", "ogd"),
        purpose_category="diagnostic",
        why_relevant="Specialist gastroenterology procedure considered if severe/persistent upper abdominal pain, vomiting blood, or refractory reflux occurs.",
        trigger_cues=("refractory acidity", "vomiting blood", "black stool", "dysphagia", "chronic upper abdominal"),
        min_duration_days=14.0,
        domain_category="specialist_procedure",
        what_it_evaluates="Direct visual lining of food pipe, stomach, and duodenum.",
        stage="specialist_later",
    ),
)


class InvestigationRecommendationEngine:
    """Produces patient-dependent investigation considerations rather than generic bundles."""

    async def recommend(
        self,
        db: AsyncSession,
        cu: CurrentUser,
        *,
        encounter_id: str,
        mem: MemoryContext,
        safety: SafetyAssessment,
        user_text: str = "",
        max_items: int = 5,
    ) -> list[dict]:
        syms = mem.short_term.get("active_symptoms", [])
        max_days = 0.0
        for s in syms:
            dtxt = str(s.get("duration") or "")
            if m := re.search(r"(\d+(?:\.\d+)?)\s*(day|week|month)", dtxt, re.I):
                val = float(m.group(1))
                mult = 7 if m.group(2).lower().startswith("w") else 30 if m.group(2).lower().startswith("m") else 1
                max_days = max(max_days, val * mult)

        signal = " ".join([
            user_text or "",
            " ".join(s.get("name", "") for s in syms),
            " ".join(t for s in syms for t in (s.get("triggers") or [])),
            " ".join(s.get("functional_impact") or "" for s in syms),
            " ".join(m.get("name", "") for m in mem.long_term.get("medications", []) if m.get("name")),
            " ".join(c.get("name", "") for c in mem.long_term.get("conditions", []) if c.get("name")),
            " ".join(safety.positive_findings),
            mem.long_term.get("demographics", {}).get("diet") or "",
        ]).lower()

        if not signal.strip():
            return []

        existing_labs = list(await db.scalars(
            select(LabResult).where(LabResult.user_id == cu.id).order_by(LabResult.observed_at.desc()).limit(30)
        ))

        candidates: list[tuple[int, InvestigationTemplate, list[str], LabResult | None, str]] = []
        for tpl in _CATALOGUE:
            if max_days < tpl.min_duration_days and tpl.min_duration_days > 0:
                continue
            matched = [cue for cue in tpl.trigger_cues if cue in signal]
            if not matched:
                continue
            urg = "urgent" if (safety.escalate_now or any(u in signal for u in tpl.urgent_cues)) else "routine"
            # Check if patient already has matching lab in records
            matched_lab = None
            for lr in existing_labs:
                tname = lr.test_name.lower()
                if any(al in tname for al in tpl.aliases) or fuzz.partial_ratio(tpl.name.lower(), tname) >= 82:
                    matched_lab = lr
                    break
            score = len(matched) * 2 + (3 if urg == "urgent" else 0)
            candidates.append((score, tpl, matched, matched_lab, urg))

        candidates.sort(key=lambda x: x[0], reverse=True)
        picked = candidates[:max_items]

        # Clear older encounter investigations for clean refresh
        old = list(await db.scalars(
            select(ClinicalInvestigation).where(ClinicalInvestigation.user_id == cu.id, ClinicalInvestigation.encounter_id == encounter_id)
        ))
        for row in old:
            await db.delete(row)

        out: list[dict] = []
        for _, tpl, matched, matched_lab, urg in picked:
            in_rec = matched_lab is not None
            rec_row = ClinicalInvestigation(
                user_id=cu.id,
                encounter_id=encounter_id,
                name=tpl.name,
                purpose_category=tpl.purpose_category,
                urgency=urg,
                clinician_decision_required=True,
                rationale_enc=cu.cipher.enc(tpl.why_relevant),
                supporting_findings_enc=cu.cipher.enc(json.dumps(matched, ensure_ascii=False)),
                limitations_enc=cu.cipher.enc(tpl.limitations),
                already_in_records=in_rec,
                matched_lab_id=matched_lab.id if matched_lab else None,
            )
            db.add(rec_row)
            out.append({
                "investigation_name": tpl.name,
                "category": tpl.purpose_category,
                "why_relevant": tpl.why_relevant,
                "what_it_evaluates": tpl.what_it_evaluates or tpl.why_relevant,
                "what_it_provides": tpl.what_it_evaluates or tpl.why_relevant,
                "preparation": tpl.preparation,
                "supporting_findings": matched,
                "urgency": urg,
                "urgency_tier": "urgent" if urg == "urgent" else ("conditional" if tpl.stage == "initial_consideration" else "optional"),
                "physical_exam_first": True,
                "clinician_decision_required": True,
                "limitations": tpl.limitations,
                "already_in_records": in_rec,
                "existing_record_summary": (
                    f"{matched_lab.test_name}: {matched_lab.value or matched_lab.value_text} {matched_lab.unit or ''} ({matched_lab.observed_at.strftime('%Y-%m-%d')})"
                    if matched_lab else None
                ),
            })

        await db.commit()
        return out


def recommend_for_turn(
    signal_text: str,
    *,
    duration_days: float = 0.0,
    urgent: bool = False,
    emergency: bool = False,
    pregnant: bool = False,
    max_items: int = 4,
) -> list[dict]:
    """Stateless, clinically conservative test recommendation helper for real-time chat/voice turns.

    Never orders tests as mandatory, never dumps broad panels, respects pregnancy radiation caution,
    and avoids routine test lists when immediate emergency care is needed.
    """
    if emergency:
        return []
    sig = (signal_text or "").lower()
    if not sig.strip():
        return []
    days = duration_days
    if days <= 0.0:
        if re.search(r"অনেকদিন|বহুদিন|মাস|বছর|long time|chronic|months?|years?", sig):
            days = 21.0
        else:
            for m in re.finditer(r"(\d+)\s*(?:weeks?|সপ্তাহ|हफ्त[ाे])", sig):
                days = max(days, float(m.group(1)) * 7.0)
            for m in re.finditer(r"(\d+)\s*(?:days?|din|দিন|दिन)", sig):
                days = max(days, float(m.group(1)))

    candidates: list[tuple[int, InvestigationTemplate, list[str], str]] = []
    for tpl in _CATALOGUE:
        if tpl.min_duration_days > 0 and days < tpl.min_duration_days:
            continue
        matched = [cue for cue in tpl.trigger_cues if cue in sig]
        if not matched:
            continue
        if pregnant and tpl.radiation_risk:
            continue  # avoid routine ionizing radiation recommendations in pregnancy
        urg = "urgent" if (urgent or any(u in sig for u in tpl.urgent_cues)) else "routine"
        score = len(matched) * 2 + (3 if urg == "urgent" else 0) + (1 if tpl.stage == "initial_consideration" else 0)
        candidates.append((score, tpl, matched, urg))

    candidates.sort(key=lambda x: x[0], reverse=True)
    out: list[dict] = []
    for _, tpl, matched, urg in candidates[:max_items]:
        out.append({
            "investigation_name": tpl.name,
            "domain_category": tpl.domain_category,
            "category": tpl.purpose_category,
            "stewardship_level": 2 if tpl.stage == "initial_consideration" else 4,
            "why_relevant": tpl.why_relevant,
            "what_it_evaluates": tpl.what_it_evaluates or tpl.why_relevant,
            "what_it_provides": tpl.what_it_evaluates or tpl.why_relevant,
            "preparation": tpl.preparation,
            "stage": tpl.stage,
            "priority_context": f"{urg} · {'Initial evaluation' if tpl.stage == 'initial_consideration' else 'Specialist-directed'}",
            "supporting_findings": matched,
            "urgency": urg,
            "urgency_tier": "urgent" if urg == "urgent" else ("conditional" if tpl.stage == "initial_consideration" else "optional"),
            "physical_exam_first": True,
            "clinician_decision_required": True,
            "limitations": tpl.limitations,
        })
    return out
