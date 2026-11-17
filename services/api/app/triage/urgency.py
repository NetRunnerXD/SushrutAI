"""Deterministic urgency for symptom-check conditions.

The local model's urgency labels proved unusable under constrained JSON decoding (it labels every condition with
the first enum value, "emergency" — see docs/EVALS.md, triage plan probe), so urgency is assigned here from a
reviewable table, never by the model. Unknown conditions default to "see a doctor this week".
"""
from __future__ import annotations

import re

from app.llm.orchestrator import URGENCY_FLOOR

LEVELS = ["self_care", "see_doctor_week", "see_doctor_24h", "emergency"]
RANK = {u: i for i, u in enumerate(LEVELS)}

# Usually managed at home when no danger sign is present (danger signs are asked first and override this).
SELF_CARE = re.compile(
    r"(?i)common cold|\bcold\b|upper respiratory|\burti\b|viral (fever|pharyngitis|rhinitis)|rhinitis|"
    r"allergic|hay fever|tension[- ]type|tension headache|acidity|gastritis|dyspepsia|indigestion|\bgerd\b|"
    r"acid reflux|heartburn|muscle (strain|spasm|pain)|muscular|sprain|gastroenteritis|food poisoning|"
    r"acute diarrh|\bdiarrh|traveller|motion sickness|dehydration \(mild\)|mild dehydration|influenza|\bflu\b|"
    r"covid|constipation|insect bite|heat rash|prickly heat|dandruff|acne|post[- ]nasal|laryngitis|"
    r"dry eye|eye strain|computer vision|sleep deprivation|caffeine")

# Worth a doctor's visit in the next few days; not urgent without danger signs.
WEEK = re.compile(
    r"(?i)bronchitis|sinusitis|migraine|cluster headache|tonsillitis|pharyngitis|otitis|ear infection|"
    r"conjunctivitis|asthma(?! attack| exacerbation)|copd(?! exacerbation)|hypertension|diabetes|thyroid|"
    r"anaemia|anemia|vitamin|deficien|irritable bowel|\bibs\b|peptic|ulcer|hemorrhoid|piles|fissure|"
    r"fungal|ringworm|tinea|scabies|eczema|dermatitis|psoriasis|arthritis|gout|sciatica|back pain|"
    r"spondyl|anxiety|depression|insomnia|pcos|pcod|menstrual|dysmenorrh|vaginal|candid|worm|parasit|"
    r"giardia|amoebiasis|amebiasis|cough variant|allergic asthma|reflux disease")

# Needs a doctor today (in addition to the shared must-not-miss floors in URGENCY_FLOOR).
TODAY = re.compile(
    r"(?i)asthma (attack|exacerbation)|copd exacerbation|cholera|dysentery|bacillary|shigell|"
    r"high fever|febrile|bronchiolitis|croup|whooping|pertussis|measles|mumps|chickenpox|varicella|"
    r"herpes zoster|shingles|glaucoma|kidney infection|cystitis|pelvic inflammatory|\bpid\b|"
    r"hyperglyc|hypoglyc|severe dehydration|leptospir|rickettsi|scrub typhus|filaria|kala[- ]azar|"
    r"rabies|snake|dog bite|animal bite|fracture|head injury|concussion|appendic|cholecyst|gallstone|"
    r"pancreatitis|diverticulitis|deep vein|dvt|pre-?eclampsia|threatened|miscarriage|heat stroke|"
    r"haemorrhage|hemorrhage")

# Emergencies not on the shared floor list (which is also used by the chat answer checker).
EMERGENCY = re.compile(
    r"(?i)subarachnoid|intracranial|brain (bleed|haemorrhage|hemorrhage)|cerebral (haemorrhage|hemorrhage)|"
    r"aortic dissection|testicular torsion|status epilepticus|pneumothorax|epiglottitis|cardiac tamponade|"
    r"ruptured|perforat|eclampsia(?!.*pre)|placental abruption|cauda equina")

# For these people, conditions that are usually self-care should still be seen by a doctor, and influenza/COVID
# should be seen early (antivirals, complications).
EARLY_TREATMENT = re.compile(r"(?i)influenza|\bflu\b|covid")


def condition_urgency(name: str, *, high_risk: bool = False) -> str:
    floor = "emergency" if EMERGENCY.search(name) else next((f for pat, f in URGENCY_FLOOR if pat.search(name)), None)
    if floor:
        base = floor
    elif TODAY.search(name):
        base = "see_doctor_24h"
    elif WEEK.search(name):
        base = "see_doctor_week"
    elif SELF_CARE.search(name):
        base = "self_care"
    else:
        base = "see_doctor_week"  # unknown to the table: be moderately cautious
    if high_risk:
        if EARLY_TREATMENT.search(name):
            return max(base, "see_doctor_24h", key=RANK.get)
        if base == "self_care":
            return "see_doctor_week"
    return base


# A condition drives the recommendation only while it is still reasonably likely; the more serious it is, the
# lower the probability at which it counts.
THRESHOLDS = [  # (condition urgency, min probability, recommended urgency)
    ("emergency", 0.25, "emergency"),
    ("emergency", 0.08, "see_doctor_24h"),
    ("see_doctor_24h", 0.20, "see_doctor_24h"),
    ("see_doctor_24h", 0.08, "see_doctor_week"),
    ("see_doctor_week", 0.20, "see_doctor_week"),
]


def recommend(ranked: list[dict], *, red_flag_yes: bool) -> str:
    """ranked: [{"urgency", "probability"}, ...]. A 'yes' to any danger-sign question means today at least."""
    best = "self_care"
    for c in ranked:
        for level, p_min, rec in THRESHOLDS:
            if c["urgency"] == level and c["probability"] >= p_min:
                best = max(best, rec, key=RANK.get)
                break
    if red_flag_yes:
        best = max(best, "see_doctor_24h", key=RANK.get)
    return best


def recommended_specialist(ranked: list[dict], rec: str) -> str | None:
    """Who to see: the most likely condition among those serious enough to set the recommendation. When that is
    an emergency-class condition (or only a danger-sign answer raised the level), the first stop is a general
    physician or the emergency department, not a specialist appointment."""
    if rec == "self_care":
        return None
    lead = next((c for c in ranked if RANK.get(c["urgency"], 0) >= RANK[rec]), None)
    if lead is None or lead["urgency"] == "emergency":
        return "General Physician"
    return lead.get("specialist") or "General Physician"
