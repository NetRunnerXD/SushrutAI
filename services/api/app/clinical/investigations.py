"""Phase 4: Patient-dependent Clinical Investigation Recommendation Engine.

Selects investigations tailored to the patient's symptoms, duration, severity, medical history,
age, pregnancy/radiation/renal safety factors, and differential hypotheses (NEVER a universal
test package or casual advanced/invasive testing). Checks existing LabResults in the patient's
chart, distinguishes initial evaluation vs. tests considered later, and never advises delaying
emergency care for routine tests.
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
    group: str  # A. Blood | B. Urine & Stool | C. Imaging | D. Cardiac | E. Neurological | F. Respiratory | G. Specialist
    aliases: tuple[str, ...]
    purpose_category: str  # screening | diagnostic | monitoring
    why_relevant: str
    what_it_evaluates: str
    trigger_cues: tuple[str, ...]
    min_duration_days: float = 0.0
    urgent_cues: tuple[str, ...] = ()
    later_only: bool = False
    invasive: bool = False
    radiation: bool = False
    contrast_risk: bool = False
    limitations: str = "A single test result must be interpreted alongside physical examination by your treating doctor."


_CATALOGUE: tuple[InvestigationTemplate, ...] = (
    # A. Blood Investigations
    InvestigationTemplate(
        name="Complete Blood Count (CBC) & Haemoglobin",
        group="Blood Investigations",
        aliases=("cbc", "haemoglobin", "hemoglobin", "hb", "platelets", "wbc"),
        purpose_category="diagnostic",
        why_relevant="Checks for anaemia, infection markers, or low platelets in prolonged fever or weakness.",
        what_it_evaluates="Red blood cells (haemoglobin), white blood cell response, and platelet count.",
        trigger_cues=("weakness", "dizziness", "fever", "head heaviness", "malaise", "pale", "fatigue", "bleeding", "দুর্বল", "জ্বর", "बुखार"),
        min_duration_days=2.0,
        urgent_cues=("bleeding", "petechiae", "fainting"),
    ),
    InvestigationTemplate(
        name="Blood Glucose (Fasting / Random)",
        group="Blood Investigations",
        aliases=("glucose", "blood sugar", "rbs", "fbs", "ppbs", "sugar"),
        purpose_category="screening",
        why_relevant="Evaluates low or high blood sugar as a contributor to dizziness, weakness, sweating, or fatigue.",
        what_it_evaluates="Current blood glucose level to rule out hypoglycaemia or hyperglycaemia.",
        trigger_cues=("dizziness", "weakness", "diabetes", "metformin", "glimepiride", "insulin", "tremor", "sweating", "head heaviness"),
    ),
    InvestigationTemplate(
        name="HbA1c",
        group="Blood Investigations",
        aliases=("hba1c", "a1c", "glycated"),
        purpose_category="monitoring",
        why_relevant="Measures 3-month average blood sugar control for existing diabetes or persistent metabolic risk.",
        what_it_evaluates="Long-term glycaemic control over the past 8–12 weeks.",
        trigger_cues=("diabetes", "metformin", "glimepiride", "insulin", "high sugar", "সুগার"),
    ),
    InvestigationTemplate(
        name="Lipid Profile",
        group="Blood Investigations",
        aliases=("lipid", "cholesterol", "triglyceride", "ldl", "hdl"),
        purpose_category="screening",
        why_relevant="Evaluates cardiovascular metabolic risk alongside hypertension, diabetes, or chest risk assessment.",
        what_it_evaluates="Total cholesterol, LDL, HDL, and triglycerides.",
        trigger_cues=("cholesterol", "lipid", "hypertension", "diabetes", "angina", "chest pain"),
        later_only=True,
    ),
    InvestigationTemplate(
        name="Thyroid Function Tests (TSH / Free T4)",
        group="Blood Investigations",
        aliases=("tsh", "thyroid", "t3", "t4"),
        purpose_category="screening",
        why_relevant="Assesses thyroid imbalance when unexplained chronic fatigue, palpitations, or weight changes persist.",
        what_it_evaluates="Thyroid hormone regulation (hypothyroidism or hyperthyroidism).",
        trigger_cues=("thyroid", "palpitation", "fatigue", "weight gain", "weight loss", "थायराइड"),
        min_duration_days=14.0,
    ),
    InvestigationTemplate(
        name="Liver Function Tests (LFT)",
        group="Blood Investigations",
        aliases=("lft", "sgpt", "sgot", "alt", "ast", "bilirubin", "liver"),
        purpose_category="diagnostic",
        why_relevant="Helpful if there is yellowing of eyes (jaundice), upper abdominal pain, persistent nausea, or dark urine.",
        what_it_evaluates="Liver enzymes (ALT/AST), bilirubin, and liver protein synthesis.",
        trigger_cues=("jaundice", "yellow", "right upper abdomen", "nausea", "hepatitis", "alcohol", "dark urine", "জন্ডিস", "पीलिया"),
    ),
    InvestigationTemplate(
        name="Kidney Function Tests (KFT / Creatinine & BUN)",
        group="Blood Investigations",
        aliases=("creatinine", "bun", "kft", "rft", "kidney"),
        purpose_category="monitoring",
        why_relevant="Checks kidney filtration before or during chronic BP/diabetes medicines, swelling, or dehydration.",
        what_it_evaluates="Serum creatinine, urea/BUN, and estimated kidney filtration.",
        trigger_cues=("kidney", "bp", "hypertension", "swelling", "urine", "diabetes", "telmisartan", "creatinine"),
    ),
    InvestigationTemplate(
        name="Serum Electrolytes (Sodium, Potassium)",
        group="Blood Investigations",
        aliases=("electrolytes", "sodium", "potassium", "na+", "k+"),
        purpose_category="diagnostic",
        why_relevant="Checks salt imbalances from vomiting, diarrhea, poor oral intake, heat exposure, or diuretic/BP medicines.",
        what_it_evaluates="Sodium and potassium balance affecting hydration, nerves, and heart rhythm.",
        trigger_cues=("vomiting", "diarrhea", "poor oral intake", "dehydration", "bp", "amlodipine", "telmisartan", "furosemide", "severe weakness"),
    ),
    InvestigationTemplate(
        name="ESR / CRP (Inflammatory Markers)",
        group="Blood Investigations",
        aliases=("esr", "crp", "c-reactive"),
        purpose_category="diagnostic",
        why_relevant="Helps gauge systemic inflammation or infection when prolonged fever or multi-joint swelling is present.",
        what_it_evaluates="Non-specific markers of active inflammation or infection.",
        trigger_cues=("prolonged fever", "joint swelling", "morning stiffness", "rheumatoid", "vasculitis", "autoimmune"),
        min_duration_days=5.0,
        later_only=True,
    ),
    InvestigationTemplate(
        name="Iron Studies / Ferritin & Vitamin B12",
        group="Blood Investigations",
        aliases=("ferritin", "iron", "tibc", "b12", "vitamin b12", "cobalamin"),
        purpose_category="diagnostic",
        why_relevant="Identifies iron or B12 depletion when persistent weakness, pallor, or nerve tingling is present.",
        what_it_evaluates="Iron stores (ferritin) and Vitamin B12 levels.",
        trigger_cues=("anaemia", "anemia", "low haemoglobin", "heavy periods", "tingling", "numbness", "metformin", "persistent fatigue"),
        min_duration_days=5.0,
    ),
    # B. Urine and Stool Investigations
    InvestigationTemplate(
        name="Urine Routine Examination (Urinalysis)",
        group="Urine & Stool Investigations",
        aliases=("urine", "urinalysis", "pus cells"),
        purpose_category="diagnostic",
        why_relevant="Evaluates urinary tract infection, protein loss, or dehydration when burning urination, flank pain, or fever occurs.",
        what_it_evaluates="Pus cells, red cells, sugar, protein, and signs of urinary infection.",
        trigger_cues=("burning urination", "dysuria", "urinary", "flank pain", "foamy urine", "fever"),
    ),
    InvestigationTemplate(
        name="Urine Culture & Sensitivity",
        group="Urine & Stool Investigations",
        aliases=("urine culture",),
        purpose_category="diagnostic",
        why_relevant="Identifies the exact bacteria and targeted treatment when urinary infection is recurrent or accompanied by fever.",
        what_it_evaluates="Bacterial growth and antibiotic sensitivity in urine.",
        trigger_cues=("recurrent urinary", "burning urination", "pyelonephritis", "flank pain"),
        min_duration_days=2.0,
        later_only=True,
    ),
    InvestigationTemplate(
        name="Stool Routine Examination / Culture",
        group="Urine & Stool Investigations",
        aliases=("stool", "stool routine", "stool culture"),
        purpose_category="diagnostic",
        why_relevant="Evaluates persistent diarrhea, blood/mucus in stool, or suspected enteric/parasitic infection.",
        what_it_evaluates="Microscopic blood, pus cells, parasites, or bacterial pathogens in stool.",
        trigger_cues=("blood in stool", "persistent diarrhea", "dysentery", "mucus in stool"),
        min_duration_days=3.0,
    ),
    # C. Imaging
    InvestigationTemplate(
        name="X-ray (Targeted Site)",
        group="Imaging",
        aliases=("x-ray", "xray", "radiograph"),
        purpose_category="diagnostic",
        why_relevant="Checks for bone fracture, joint alignment, or focal bony injury after trauma or persistent joint pain.",
        what_it_evaluates="Bone integrity and joint space.",
        trigger_cues=("fracture", "fall", "dislocation", "cannot walk", "bone pain", "joint swelling"),
        radiation=True,
    ),
    InvestigationTemplate(
        name="Ultrasound (Abdomen / Pelvis / Targeted)",
        group="Imaging",
        aliases=("ultrasound", "usg", "sonography"),
        purpose_category="diagnostic",
        why_relevant="Radiation-free imaging to evaluate persistent abdominal/flank pain, gallstones, liver/kidney structure, or pelvic symptoms.",
        what_it_evaluates="Solid abdominal organs (liver, gallbladder, kidneys) and pelvic structures without radiation.",
        trigger_cues=("right upper abdomen", "jaundice", "flank pain", "kidney stone", "pelvic pain", "pregnant", "gallstone"),
        min_duration_days=2.0,
    ),
    InvestigationTemplate(
        name="Doppler Ultrasound",
        group="Imaging",
        aliases=("doppler", "venous doppler"),
        purpose_category="diagnostic",
        why_relevant="Evaluates blood flow when unilateral leg swelling, calf pain, or suspected vascular compromise occurs.",
        what_it_evaluates="Venous or arterial blood flow and clots (e.g., DVT).",
        trigger_cues=("unilateral leg swelling", "calf pain", "dvt", "varicose", "claudication"),
        urgent_cues=("unilateral leg swelling", "calf pain"),
    ),
    InvestigationTemplate(
        name="CT Scan (Clinician-Directed)",
        group="Imaging",
        aliases=("ct scan", "ct ", "computed tomography"),
        purpose_category="diagnostic",
        why_relevant="Considered by a clinician only when detailed cross-sectional imaging is needed (e.g., suspected stone, trauma, or complex abdomen).",
        what_it_evaluates="Detailed cross-sectional organ or bone anatomy.",
        trigger_cues=("renal calculus", "head injury", "appendicitis", "severe trauma"),
        later_only=True,
        radiation=True,
        contrast_risk=True,
        limitations="Involves ionizing radiation and sometimes contrast dye; ordered strictly after clinical examination.",
    ),
    InvestigationTemplate(
        name="MRI (Clinician-Directed)",
        group="Imaging",
        aliases=("mri", "magnetic resonance"),
        purpose_category="diagnostic",
        why_relevant="Radiation-free high-detail imaging considered later by a specialist for persistent spine, nerve, or soft-tissue pathology.",
        what_it_evaluates="Soft tissues, spinal cord, nerve roots, and joints without radiation.",
        trigger_cues=("spinal cord compression", "leg numbness", "disc prolapse", "persistent joint locking"),
        min_duration_days=7.0,
        later_only=True,
    ),
    # D. Cardiac Investigations
    InvestigationTemplate(
        name="12-lead ECG",
        group="Cardiac Investigations",
        aliases=("ecg", "ekg", "electrocardiogram"),
        purpose_category="diagnostic",
        why_relevant="Evaluates heart rhythm and ischemia urgently when chest symptoms, palpitations, fainting, or severe unexplained dizziness occur.",
        what_it_evaluates="Electrical rhythm, conduction intervals, and signs of cardiac strain or ischemia.",
        trigger_cues=("chest pain", "palpitation", "fainting", "shortness of breath", "blackout", "orthostatic", "বুক ধড়ফড়"),
        urgent_cues=("chest pain", "fainting", "shortness of breath", "palpitation"),
        limitations="A single resting ECG only captures rhythm at that moment; intermittent arrhythmias may need longer monitoring.",
    ),
    InvestigationTemplate(
        name="Cardiac Biomarkers (Troponin)",
        group="Cardiac Investigations",
        aliases=("troponin", "cardiac biomarkers", "ck-mb"),
        purpose_category="diagnostic",
        why_relevant="Emergency hospital test when acute chest pain or suspected heart muscle injury is evaluated.",
        what_it_evaluates="Heart muscle injury markers in acute coronary syndrome.",
        trigger_cues=("chest pain", "angina", "sweating", "crushing chest"),
        urgent_cues=("chest pain", "angina", "sweating", "crushing chest"),
        limitations="Performed immediately in an emergency/hospital setting — never wait for an outpatient lab if chest pain is active.",
    ),
    InvestigationTemplate(
        name="Echocardiography (2D Echo)",
        group="Cardiac Investigations",
        aliases=("echo", "echocardiogram", "2d echo"),
        purpose_category="diagnostic",
        why_relevant="Ordered by a physician when structural heart disease, valve issues, or exertional breathlessness/syncope is suspected.",
        what_it_evaluates="Heart pumping function (ejection fraction), chambers, and heart valves.",
        trigger_cues=("heart failure", "exertional breathlessness", "swollen ankles", "cardiac syncope", "valve"),
        later_only=True,
    ),
    InvestigationTemplate(
        name="Holter Monitoring",
        group="Cardiac Investigations",
        aliases=("holter", "ambulatory ecg"),
        purpose_category="monitoring",
        why_relevant="Tracks intermittent heart rhythm changes if episodic palpitations or recurrent near-fainting persist despite normal resting ECG.",
        what_it_evaluates="Continuous 24–48h heart rhythm recording during daily activities.",
        trigger_cues=("intermittent palpitation", "recurrent fainting", "episodic blackout", "arrhythmia"),
        min_duration_days=3.0,
        later_only=True,
    ),
    # E. Neurological Investigations
    InvestigationTemplate(
        name="CT Brain / MRI Brain (Clinician-Directed)",
        group="Neurological Investigations",
        aliases=("ct brain", "mri brain", "brain scan"),
        purpose_category="diagnostic",
        why_relevant="Urgent or specialist neuroimaging when focal weakness, slurred speech, seizures, or severe sudden headache occur.",
        what_it_evaluates="Stroke, bleeding, structural lesions, or intracranial pressure.",
        trigger_cues=("stroke", "slurred speech", "sudden weakness", "head injury", "seizure", "paralysis"),
        urgent_cues=("stroke", "slurred speech", "sudden weakness", "paralysis"),
        later_only=True,
        radiation=True,
    ),
    InvestigationTemplate(
        name="EEG (Electroencephalogram)",
        group="Neurological Investigations",
        aliases=("eeg", "electroencephalogram"),
        purpose_category="diagnostic",
        why_relevant="Evaluates brain electrical activity when seizures or unexplained episodes of loss of awareness are being worked up by a neurologist.",
        what_it_evaluates="Seizure-related electrical discharges in the brain.",
        trigger_cues=("seizure", "convulsion", "epilepsy"),
        later_only=True,
    ),
    InvestigationTemplate(
        name="Nerve Conduction Studies (NCS) / EMG",
        group="Neurological Investigations",
        aliases=("ncs", "nerve conduction", "emg"),
        purpose_category="diagnostic",
        why_relevant="Considered by a neurologist when persistent tingling, numbness, or progressive muscle weakness suggests peripheral neuropathy.",
        what_it_evaluates="Peripheral nerve signal speed and muscle electrical response.",
        trigger_cues=("peripheral neuropathy", "progressive numbness", "muscle wasting", "diabetic neuropathy"),
        min_duration_days=14.0,
        later_only=True,
    ),
    # F. Respiratory Investigations
    InvestigationTemplate(
        name="Chest X-ray",
        group="Respiratory Investigations",
        aliases=("chest x-ray", "cxr", "chest xray"),
        purpose_category="diagnostic",
        why_relevant="Evaluates lungs and airways when persistent cough, fever with breathlessness, or suspected pneumonia/TB is present.",
        what_it_evaluates="Lung fields for infection, fluid, or chronic airway changes.",
        trigger_cues=("persistent cough", "cough", "shortness of breath", "tb", "tuberculosis", "pneumonia", "coughing blood"),
        min_duration_days=3.0,
        urgent_cues=("coughing blood", "severe breathlessness"),
        radiation=True,
    ),
    InvestigationTemplate(
        name="Spirometry / Pulmonary Function Test (PFT)",
        group="Respiratory Investigations",
        aliases=("spirometry", "pft", "pulmonary function"),
        purpose_category="diagnostic",
        why_relevant="Measures airflow obstruction when recurring wheezing, asthma, or chronic breathlessness is evaluated.",
        what_it_evaluates="Lung volumes and reversible airway narrowing.",
        trigger_cues=("asthma", "copd", "wheezing", "chronic cough"),
        min_duration_days=7.0,
        later_only=True,
    ),
    # G. Other Specialist Investigations (strictly clinician-gated)
    InvestigationTemplate(
        name="Upper GI Endoscopy / Colonoscopy (Specialist-Directed)",
        group="Specialist Investigations",
        aliases=("endoscopy", "colonoscopy", "ogd"),
        purpose_category="diagnostic",
        why_relevant="Invasive specialist procedure considered only for alarm GI features (persistent difficulty swallowing, GI bleeding, or chronic unexplained symptoms).",
        what_it_evaluates="Direct visualization of upper GI or colonic mucosa.",
        trigger_cues=("dysphagia", "black stool", "gi bleed", "ibd", "crohn", "ulcerative colitis"),
        min_duration_days=7.0,
        later_only=True,
        invasive=True,
        limitations="Invasive procedure requiring gastroenterologist evaluation and consent; never a first-line routine check.",
    ),
    InvestigationTemplate(
        name="Biopsy / Histopathology (Specialist-Directed)",
        group="Specialist Investigations",
        aliases=("biopsy", "histopathology", "fnac"),
        purpose_category="diagnostic",
        why_relevant="Considered only when a specialist needs tissue confirmation for a persistent unexplained lump, non-healing ulcer, or suspected malignancy.",
        what_it_evaluates="Cellular tissue architecture under microscopy.",
        trigger_cues=("persistent lump", "biopsy", "malignancy", "carcinoma", "lymphoma"),
        min_duration_days=14.0,
        later_only=True,
        invasive=True,
        limitations="Invasive sampling performed only after imaging/clinical evaluation by a specialist.",
    ),
)


class InvestigationRecommendationEngine:
    """Produces patient-dependent, safety-filtered investigation considerations."""

    async def recommend(
        self,
        db: AsyncSession,
        cu: CurrentUser,
        *,
        encounter_id: str,
        mem: MemoryContext,
        safety: SafetyAssessment,
        user_text: str = "",
        differentials: list[dict] | None = None,
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

        demo = mem.long_term.get("demographics", {})
        pregnant = bool(demo.get("pregnant")) or any(w in (user_text or "").lower() for w in ("pregnant", "pregnancy", "trimester", "গর্ভবতী", "गर्भवती"))
        meds_text = " ".join(m.get("name", "") for m in mem.long_term.get("medications", []) if m.get("name"))
        conds_text = " ".join(c.get("name", "") for c in mem.long_term.get("conditions", []) if c.get("name"))
        allergies_text = " ".join(a.get("name", "") for a in mem.long_term.get("allergies", []) if a.get("name"))
        has_renal_risk = any(w in (conds_text + " " + user_text).lower() for w in ("kidney", "ckd", "renal", "creatinine"))
        has_contrast_allergy = any(w in allergies_text.lower() for w in ("contrast", "iodine", "dye"))

        signal = " ".join([
            user_text or "",
            " ".join(s.get("name", "") for s in syms),
            " ".join(t for s in syms for t in (s.get("triggers") or [])),
            " ".join(s.get("functional_impact") or "" for s in syms),
            meds_text,
            conds_text,
            " ".join(safety.positive_findings),
            " ".join(d.get("explanation", "") for d in (differentials or [])),
            demo.get("diet") or "",
        ]).lower()

        if not signal.strip():
            return []

        existing_labs = list(await db.scalars(
            select(LabResult).where(LabResult.user_id == cu.id).order_by(LabResult.observed_at.desc()).limit(30)
        ))

        candidates: list[tuple[int, InvestigationTemplate, list[str], LabResult | None, str, str, str | None]] = []
        for tpl in _CATALOGUE:
            if max_days < tpl.min_duration_days and tpl.min_duration_days > 0:
                continue
            matched = [cue for cue in tpl.trigger_cues if cue in signal]
            if not matched:
                continue

            is_urgent = bool(safety.escalate_now or any(u in signal for u in tpl.urgent_cues))
            # Rule 12: Never advise delaying emergency care for routine/later tests
            if safety.escalate_now and not any(u in signal for u in tpl.urgent_cues) and tpl.later_only:
                continue

            urg = "urgent" if is_urgent else "routine"
            priority_ctx = "urgent_evaluation" if is_urgent else ("considered_later" if tpl.later_only else "initial_evaluation")

            safety_notes: list[str] = []
            if pregnant and tpl.radiation:
                safety_notes.append("Pregnancy caution: avoid ionizing radiation unless strictly essential; discuss radiation-free alternatives (e.g., ultrasound/MRI) with your doctor.")
            if tpl.contrast_risk and (has_renal_risk or has_contrast_allergy):
                safety_notes.append("Kidney/allergy caution: inform your doctor about kidney history or contrast/iodine allergy before any contrast scan.")
            if tpl.invasive:
                safety_notes.append("Invasive test: considered only after specialist examination.")

            matched_lab = None
            for lr in existing_labs:
                tname = lr.test_name.lower()
                if any(al in tname for al in tpl.aliases) or fuzz.partial_ratio(tpl.name.lower(), tname) >= 82:
                    matched_lab = lr
                    break

            score = len(matched) * 2 + (4 if is_urgent else 0) - (2 if tpl.later_only else 0)
            candidates.append((score, tpl, matched, matched_lab, urg, priority_ctx, " ".join(safety_notes) or None))

        candidates.sort(key=lambda x: x[0], reverse=True)
        picked = candidates[:max_items]

        old = list(await db.scalars(
            select(ClinicalInvestigation).where(ClinicalInvestigation.user_id == cu.id, ClinicalInvestigation.encounter_id == encounter_id)
        ))
        for row in old:
            await db.delete(row)

        out: list[dict] = []
        for _, tpl, matched, matched_lab, urg, priority_ctx, safety_note in picked:
            in_rec = matched_lab is not None
            lim_full = f"{tpl.limitations} {safety_note}".strip() if safety_note else tpl.limitations
            rec_row = ClinicalInvestigation(
                user_id=cu.id,
                encounter_id=encounter_id,
                name=tpl.name,
                purpose_category=tpl.purpose_category,
                urgency=urg,
                clinician_decision_required=True,
                rationale_enc=cu.cipher.enc(tpl.why_relevant),
                supporting_findings_enc=cu.cipher.enc(json.dumps(matched, ensure_ascii=False)),
                limitations_enc=cu.cipher.enc(lim_full),
                already_in_records=in_rec,
                matched_lab_id=matched_lab.id if matched_lab else None,
            )
            db.add(rec_row)
            out.append({
                "investigation_name": tpl.name,
                "investigation_group": tpl.group,
                "category": tpl.purpose_category,
                "why_relevant": tpl.why_relevant,
                "what_it_evaluates": tpl.what_it_evaluates,
                "priority_context": priority_ctx,
                "supporting_findings": matched,
                "urgency": urg,
                "clinician_decision_required": True,
                "safety_considerations": safety_note,
                "limitations": lim_full,
                "already_in_records": in_rec,
                "existing_record_summary": (
                    f"{matched_lab.test_name}: {matched_lab.value or matched_lab.value_text} {matched_lab.unit or ''} ({matched_lab.observed_at.strftime('%Y-%m-%d')})"
                    if matched_lab else None
                ),
            })

        await db.commit()
        return out
