"""Modular, Versioned Medical Knowledge Graph & Differential Reasoning Engine.

Connects symptoms, associated symptoms, possible explanations (without diagnostic certainty),
warning signs, departments, specialists, qualifications, and 4-level investigation stewardship.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

KNOWLEDGE_GRAPH_VERSION = "2026.11.1"
LAST_UPDATED = "2026-11-26"
REVIEW_STATUS = "evidence_informed_prototype_pending_clinician_signoff"


@dataclass(frozen=True)
class DifferentialHypothesis:
    name: str
    supporting_cues: tuple[str, ...]
    contradictory_cues: tuple[str, ...]
    distinguishing_topic: str
    why: str


@dataclass(frozen=True)
class KnowledgeNode:
    node_id: str
    label: str
    cues: tuple[str, ...]
    associated_symptoms: tuple[str, ...]
    hypotheses: tuple[DifferentialHypothesis, ...]
    warning_signs: tuple[str, ...]
    department: str
    specialist: str
    qualification: str
    initial_tests: tuple[str, ...]
    conditional_tests: tuple[str, ...]
    not_justified_note: str
    source: str
    rationale: str
    applicability: str
    limitations: str
    last_updated: str = LAST_UPDATED
    review_status: str = REVIEW_STATUS


KNOWLEDGE_NODES: tuple[KnowledgeNode, ...] = (
    KnowledgeNode(
        node_id="abdominal_gi",
        label="Abdominal & Gastrointestinal Presentation",
        cues=("stomach", "abdom", "belly", "acidity", "gastr", "nausea", "vomit", "diarrh", "loose motion", "পেটে", "বমি", "অ্যাসিডিটি", "पेट"),
        associated_symptoms=("fever", "nausea", "vomiting", "loose motions", "burning urination", "jaundice"),
        hypotheses=(
            DifferentialHypothesis(
                name="Acid-peptic irritation / gastritis",
                supporting_cues=("burning", "acidity", "after meal", "upper", "reflux"),
                contradictory_cues=("lower right", "jaundice", "high fever"),
                distinguishing_topic="location",
                why="Upper abdominal burning or meal-related discomfort often points to acid-peptic irritation.",
            ),
            DifferentialHypothesis(
                name="Acute gastroenteritis / GI infection",
                supporting_cues=("fever", "nausea", "vomit", "loose motion", "diarrh"),
                contradictory_cues=("weeks", "months"),
                distinguishing_topic="associated",
                why="Combination of stomach pain, nausea, and fever suggests possible infective or inflammatory GI irritation.",
            ),
            DifferentialHypothesis(
                name="Hepatobiliary or surgical abdominal cause (must not miss)",
                supporting_cues=("right upper", "right lower", "jaundice", "severe", "rigid"),
                contradictory_cues=("mild", "intermittent"),
                distinguishing_topic="warning_signs",
                why="Localized severe pain, persistent vomiting, or jaundice requires prompt physician/surgical evaluation.",
            ),
        ),
        warning_signs=("Severe or worsening localized pain", "Persistent vomiting", "Black or bloody stools", "Yellowing of eyes"),
        department="Gastroenterology",
        specialist="Gastroenterologist",
        qualification="MD / DNB Gastroenterology",
        initial_tests=("Complete Blood Count (CBC)", "Liver Function Tests (LFT)", "Serum Electrolytes (Sodium, Potassium)"),
        conditional_tests=("Abdominal Ultrasound", "Stool Routine & Culture"),
        not_justified_note="Mild, brief (<48h) indigestion without fever or red flags usually needs clinical review before imaging or broad lab panels.",
        source="WHO / ACG Clinical Evaluation Principles",
        rationale="Distinguishes uncomplicated dyspepsia/gastritis from infective gastroenteritis or hepatobiliary/surgical pathology.",
        applicability="Adult and adolescent abdominal/GI complaints",
        limitations="Physical abdominal palpation is essential to rule out surgical tenderness.",
    ),
    KnowledgeNode(
        node_id="headache_neuro",
        label="Headache & Neurological Presentation",
        cues=("headache", "migraine", "vertigo", "dizz", "numbness", "tingling", "seizure", "মাথাব্যথা", "ঝিনঝিন", "सिरदर्द"),
        associated_symptoms=("vomiting", "fever", "blurred vision", "neck stiffness", "limb weakness"),
        hypotheses=(
            DifferentialHypothesis(
                name="Tension-type headache or migraine",
                supporting_cues=("throbbing", "one side", "light", "stress", "gradual"),
                contradictory_cues=("sudden", "thunderclap", "fever", "weakness"),
                distinguishing_topic="onset",
                why="Gradual or recurrent headaches without focal deficits are commonly primary headache disorders.",
            ),
            DifferentialHypothesis(
                name="Secondary headache or neuropathic/central cause (must not miss)",
                supporting_cues=("fever", "numbness", "tingling", "vision", "sudden", "vomit"),
                contradictory_cues=(),
                distinguishing_topic="warning_signs",
                why="Focal numbness, visual change, fever, or sudden severe onset requires prompt neurological assessment.",
            ),
        ),
        warning_signs=("Sudden 'worst-ever' headache", "One-sided weakness or slurred speech", "High fever with neck stiffness", "New vision loss"),
        department="Neurology",
        specialist="Neurologist",
        qualification="MD / DM Neurology",
        initial_tests=("Blood pressure check", "Blood glucose (fasting / random)"),
        conditional_tests=("MRI Brain / CT Head (only if red flags or focal deficits)"),
        not_justified_note="Routine neuroimaging (CT/MRI) is not justified for uncomplicated brief headache without focal signs.",
        source="NICE CG150 / AAN Headache Stewardship",
        rationale="Separates benign primary headaches from secondary neurological emergencies requiring imaging.",
        applicability="Headache, dizziness, and neuropathic symptoms",
        limitations="Fundoscopy and cranial nerve examination cannot be performed remotely.",
    ),
    KnowledgeNode(
        node_id="chest_cardiac",
        label="Chest Discomfort & Cardiovascular Presentation",
        cues=("chest pain", "chest tightness", "chest discomfort", "palpitation", "angina", "বুকে ব্যথা", "বুকে চাপ", "सीने में दर्द"),
        associated_symptoms=("sweating", "shortness of breath", "arm/jaw pain", "dizziness"),
        hypotheses=(
            DifferentialHypothesis(
                name="Exertional or ischaemic cardiac discomfort (must not miss)",
                supporting_cues=("walking", "exertion", "tightness", "pressure", "sweat"),
                contradictory_cues=(),
                distinguishing_topic="aggravating",
                why="Exertional chest discomfort or tightness must be evaluated promptly to exclude cardiac ischaemia.",
            ),
            DifferentialHypothesis(
                name="Non-cardiac chest wall or reflux discomfort",
                supporting_cues=("acidity", "burning", "tender", "meal"),
                contradictory_cues=("walking", "sweating", "jaw"),
                distinguishing_topic="associated",
                why="Gastro-oesophageal reflux or musculoskeletal strain can mimic chest discomfort but cardiac causes are ruled out first.",
            ),
        ),
        warning_signs=("Pain spreading to arm, jaw, or back", "Cold sweats", "Breathlessness at rest", "Fainting"),
        department="Cardiology",
        specialist="Cardiologist",
        qualification="MD General Medicine / DM Cardiology",
        initial_tests=("12-lead ECG", "Troponin (if acute/urgent)"),
        conditional_tests=("Echocardiography", "Holter Monitoring"),
        not_justified_note="Do not delay ECG or urgent care if pain is active or exertional.",
        source="ESC / ACC Chest Pain Guidelines",
        rationale="Prioritizes ruling out acute coronary syndrome and arrhythmia safely.",
        applicability="Chest discomfort, palpitations, exertional symptoms",
        limitations="Resting ECG alone does not exclude unstable angina.",
    ),
    KnowledgeNode(
        node_id="respiratory_cough_dyspnea",
        label="Respiratory & Airway Presentation",
        cues=("cough", "wheez", "asthma", "breathless", "shortness of breath", "sputum", "কাশি", "শ্বাসকষ্ট", "खांसी"),
        associated_symptoms=("fever", "wheezing", "chest tightness", "night cough"),
        hypotheses=(
            DifferentialHypothesis(
                name="Acute viral upper/lower respiratory infection",
                supporting_cues=("fever", "sore throat", "days"),
                contradictory_cues=("weeks", "months", "wheezing"),
                distinguishing_topic="duration",
                why="Short-duration cough with mild upper airway symptoms is often self-limiting viral bronchitis/URI.",
            ),
            DifferentialHypothesis(
                name="Airway hyperreactivity / asthma / chronic bronchitis",
                supporting_cues=("wheez", "night", "weeks", "asthma"),
                contradictory_cues=(),
                distinguishing_topic="warning_signs",
                why="Recurrent wheezing or prolonged cough (>2–3 weeks) warrants pulmonary evaluation.",
            ),
        ),
        warning_signs=("Difficulty speaking in full sentences", "Bluish lips", "Coughing up blood", "SpO2 < 94%"),
        department="Pulmonology",
        specialist="Pulmonologist",
        qualification="MD Respiratory Medicine",
        initial_tests=("Pulse Oximetry (SpO2)",),
        conditional_tests=("Chest X-ray (PA view)", "Spirometry / PFT"),
        not_justified_note="Brief (<1 week) uncomplicated cough without fever or hypoxia rarely needs immediate Chest X-ray.",
        source="GINA / BTS Respiratory Assessment Principles",
        rationale="Avoids unnecessary radiation in acute mild cough while catching prolonged/hypoxic lung disease.",
        applicability="Cough, wheeze, dyspnoea",
        limitations="Lung auscultation and SpO2 measurement require in-person check.",
    ),
    KnowledgeNode(
        node_id="musculoskeletal_joint",
        label="Musculoskeletal & Joint Presentation",
        cues=("knee", "joint", "back pain", "arthritis", "stiffness", "shoulder", "হাঁটু", "কোমর", "जोड़"),
        associated_symptoms=("swelling", "morning stiffness", "injury", "fever"),
        hypotheses=(
            DifferentialHypothesis(
                name="Mechanical / degenerative joint strain or osteoarthritis",
                supporting_cues=("walking", "stairs", "knee", "age"),
                contradictory_cues=("morning stiffness", "multiple joints", "fever"),
                distinguishing_topic="aggravating",
                why="Weight-bearing pain worse on stairs or walking without systemic features suggests mechanical strain.",
            ),
            DifferentialHypothesis(
                name="Inflammatory / autoimmune arthritis",
                supporting_cues=("morning stiffness", "multiple joints", "swelling"),
                contradictory_cues=("injury", "fall"),
                distinguishing_topic="associated",
                why="Prolonged morning stiffness or symmetric multi-joint swelling warrants rheumatological evaluation.",
            ),
        ),
        warning_signs=("Hot red swollen joint with fever", "Inability to bear weight after injury", "Leg numbness or bladder change"),
        department="Orthopaedics",
        specialist="Orthopaedician",
        qualification="MS Orthopaedics",
        initial_tests=(),
        conditional_tests=("X-ray of affected joint", "ESR / CRP / Uric Acid (if inflammatory)"),
        not_justified_note="Mild mechanical joint ache without trauma, swelling, or systemic symptoms needs physical exam before X-rays or MRI.",
        source="ACR / NICE Musculoskeletal Triage",
        rationale="Prevents routine unnecessary imaging for simple sprains/strains.",
        applicability="Joint, spine, and musculoskeletal pain",
        limitations="Ligament stability and joint effusion require hands-on exam.",
    ),
    KnowledgeNode(
        node_id="renal_urinary",
        label="Renal & Urinary Presentation",
        cues=("urine", "frothy", "creatinine", "kidney", "renal", "dysuria", "flank", "কিডনি", "প্রস্রাব", "पेशाब"),
        associated_symptoms=("swelling", "fever", "burning urination", "flank pain"),
        hypotheses=(
            DifferentialHypothesis(
                name="Glomerular / renal parenchymal concern",
                supporting_cues=("frothy", "creatinine", "swelling", "proteinuria"),
                contradictory_cues=("burning",),
                distinguishing_topic="associated",
                why="Frothy urine, oedema, or elevated creatinine points toward Nephrology evaluation.",
            ),
            DifferentialHypothesis(
                name="Urinary tract infection or renal stone",
                supporting_cues=("burning", "flank", "fever", "stone"),
                contradictory_cues=(),
                distinguishing_topic="warning_signs",
                why="Dysuria or flank colic with fever requires urinalysis and prompt evaluation.",
            ),
        ),
        warning_signs=("High fever with flank pain", "Reduced/absent urine output", "Severe facial/leg swelling"),
        department="Nephrology",
        specialist="Nephrologist",
        qualification="DM Nephrology",
        initial_tests=("Urinalysis", "Kidney Function Tests (Creatinine / BUN)"),
        conditional_tests=("Urine Protein-to-Creatinine Ratio", "Renal Ultrasound"),
        not_justified_note="Targeted urinalysis and KFT come before CT KUB unless acute renal colic is suspected.",
        source="KDIGO / EAU Clinical Principles",
        rationale="Separates medical kidney disease (Nephrology) from stones/obstruction (Urology).",
        applicability="Urinary and renal complaints",
        limitations="Blood pressure and urine dipstick/microscopy are needed.",
    ),
    KnowledgeNode(
        node_id="dermatological_skin",
        label="Dermatological Presentation",
        cues=("rash", "itch", "eczema", "psoriasis", "fungal", "ringworm", "skin", "র‍্যাশ", "চুলকানি", "खुजली"),
        associated_symptoms=("fever", "spreading", "blisters", "lip/eye swelling"),
        hypotheses=(
            DifferentialHypothesis(
                name="Superficial dermatitis, eczema, or fungal infection",
                supporting_cues=("itch", "eczema", "patch", "ringworm"),
                contradictory_cues=("blisters", "lip swelling", "high fever"),
                distinguishing_topic="duration",
                why="Localized itchy patches without mucosal or systemic signs are typically benign dermatoses.",
            ),
        ),
        warning_signs=("Lip/tongue swelling or breathing difficulty", "Painful blistering or mucosal sores", "Rapidly spreading rash with fever"),
        department="Dermatology",
        specialist="Dermatologist",
        qualification="MD Dermatology",
        initial_tests=(),
        conditional_tests=("KOH mount / skin scraping (if fungal suspected by dermatologist)",),
        not_justified_note="Visual skin examination by a clinician is primary; blood tests are usually not justified for localized uncomplicated rashes.",
        source="BAD / IADVL Clinical Principles",
        rationale="Avoids unnecessary blood panels for localized skin conditions.",
        applicability="Skin rashes and itching",
        limitations="Visual inspection under proper lighting is essential.",
    ),
    KnowledgeNode(
        node_id="endocrine_metabolic",
        label="Endocrine & Metabolic Presentation",
        cues=("thyroid", "tsh", "diabetes", "blood sugar", "hba1c", "insulin", "থাইরয়েড", "ডায়াবেটিস", "शुगर"),
        associated_symptoms=("fatigue", "weight change", "palpitation", "excessive thirst"),
        hypotheses=(
            DifferentialHypothesis(
                name="Thyroid or glycaemic dysregulation",
                supporting_cues=("tsh", "hba1c", "sugar", "thyroid", "fatigue"),
                contradictory_cues=(),
                distinguishing_topic="history_meds",
                why="Abnormal TSH or HbA1c/blood sugar results require structured endocrine/internal medicine review.",
            ),
        ),
        warning_signs=("Severe vomiting/drowsiness with high sugar", "Confusion or sweating from low sugar"),
        department="Endocrinology",
        specialist="Endocrinologist",
        qualification="DM Endocrinology",
        initial_tests=("Thyroid Function (TSH)", "HbA1c", "Blood glucose (fasting / random)"),
        conditional_tests=("Free T4", "Lipid Profile"),
        not_justified_note="Avoid repeating HbA1c more often than every 3 months unless clinically directed.",
        source="ADA / ATA Standards of Care",
        rationale="Aligns hormone and glycaemic testing with actual report/symptom triggers.",
        applicability="Diabetes, thyroid, and metabolic concerns",
        limitations="Medication dose adjustments require an in-person clinician.",
    ),
    KnowledgeNode(
        node_id="febrile_illness",
        label="Febrile & Systemic Presentation",
        cues=("fever", "chills", "body ache", "জ্বর", "बुखार"),
        associated_symptoms=("cough", "stomach pain", "rash", "burning urination", "headache"),
        hypotheses=(
            DifferentialHypothesis(
                name="Acute self-limiting viral fever",
                supporting_cues=("1 day", "2 days", "mild", "body ache"),
                contradictory_cues=("5 days", "7 days", "jaundice", "rash"),
                distinguishing_topic="duration",
                why="Brief (<3 days) fever without localizing red flags is often viral.",
            ),
            DifferentialHypothesis(
                name="Prolonged or vector-borne / bacterial infection",
                supporting_cues=("3 days", "4 days", "5 days", "chills", "rash"),
                contradictory_cues=(),
                distinguishing_topic="associated",
                why="Fever lasting >=3 days or with rigors/rash warrants targeted blood evaluation.",
            ),
        ),
        warning_signs=("Confusion or extreme drowsiness", "Persistent vomiting", "Bleeding gums/skin spots", "Breathing difficulty"),
        department="General Medicine",
        specialist="Internal Medicine Physician",
        qualification="MD General Medicine",
        initial_tests=("Complete Blood Count (CBC)",),
        conditional_tests=("Dengue NS1 / Malaria Antigen (if endemic fever >=3 days)", "Urinalysis (only if urinary symptoms)"),
        not_justified_note="Brief (<48h) mild fever without localizing symptoms usually warrants hydration, monitoring, and clinician exam before lab panels.",
        source="NVBDCP / WHO Febrile Illness Stewardship",
        rationale="Prevents ordering broad fever panels on day 1 while escalating >=3 day fevers appropriately.",
        applicability="Fever and systemic malaise",
        limitations="Vital signs and physical exam determine need for blood tests.",
    ),
)


@dataclass
class DifferentialAssessment:
    active_nodes: list[KnowledgeNode] = field(default_factory=list)
    possible_explanations: list[dict[str, Any]] = field(default_factory=list)
    supporting_findings: list[str] = field(default_factory=list)
    contradictory_findings: list[str] = field(default_factory=list)
    missing_information: list[str] = field(default_factory=list)
    must_not_miss_warnings: list[str] = field(default_factory=list)
    uncertainty_note: str = ""
    multi_symptom_linked: bool = False
    knowledge_provenance: list[dict[str, str]] = field(default_factory=list)


def analyze_clinical_differentials(
    merged_text: str,
    *,
    symptoms: list[str] | None = None,
    answered_topics: set[str] | None = None,
    lang: str = "en",
) -> DifferentialAssessment:
    """Run multi-symptom DifferentialReasoningEngine over the modular MedicalKnowledgeGraph."""
    sig = f"{merged_text or ''} {' '.join(symptoms or [])}".lower()
    asked = answered_topics or set()

    matched_nodes: list[tuple[int, KnowledgeNode]] = []
    for node in KNOWLEDGE_NODES:
        hits = sum(1 for cue in node.cues if cue in sig)
        if hits > 0:
            matched_nodes.append((hits, node))
    matched_nodes.sort(key=lambda x: x[0], reverse=True)
    active = [n for _, n in matched_nodes[:3]]

    explanations: list[dict[str, Any]] = []
    supporting: list[str] = []
    contradictory: list[str] = []
    missing: list[str] = []
    warnings: list[str] = []
    provenance: list[dict[str, str]] = []

    for node in active:
        provenance.append({
            "node_id": node.node_id,
            "source": node.source,
            "rationale": node.rationale,
            "limitations": node.limitations,
            "last_updated": node.last_updated,
            "review_status": node.review_status,
        })
        warnings.extend(node.warning_signs[:2])
        for hyp in node.hypotheses:
            sup = [c for c in hyp.supporting_cues if c in sig]
            con = [c for c in hyp.contradictory_cues if c in sig]
            if sup:
                supporting.extend(sup)
            if con:
                contradictory.extend(con)
            if hyp.distinguishing_topic not in asked and hyp.distinguishing_topic not in missing:
                missing.append(hyp.distinguishing_topic)
            explanations.append({
                "name": hyp.name,
                "why": hyp.why,
                "supporting_findings": sup,
                "contradictory_findings": con,
                "distinguishing_topic": hyp.distinguishing_topic,
            })

    multi_linked = len(active) >= 2
    if lang == "bn":
        unc = (
            "উপলব্ধ তথ্য থেকে একাধিক সম্ভাব্য কারণ বিবেচনা করা হচ্ছে; "
            "শারীরিক পরীক্ষা ও প্রাসঙ্গিক মূল্যায়ন ছাড়া নিশ্চিত রোগ নির্ণয় সম্ভব নয়।"
        )
    elif lang == "hi":
        unc = (
            "उपलब्ध जानकारी के आधार पर एक से अधिक संभावित कारण हो सकते हैं; "
            "शारीरिक जांच के बिना पक्का निदान संभव नहीं है।"
        )
    else:
        unc = (
            "More than one explanation may account for these symptoms; "
            "confirmation requires physical examination and clinician judgment."
        )

    return DifferentialAssessment(
        active_nodes=active,
        possible_explanations=explanations[:4],
        supporting_findings=list(dict.fromkeys(supporting)),
        contradictory_findings=list(dict.fromkeys(contradictory)),
        missing_information=missing[:4],
        must_not_miss_warnings=list(dict.fromkeys(warnings))[:4],
        uncertainty_note=unc,
        multi_symptom_linked=multi_linked,
        knowledge_provenance=provenance,
    )
