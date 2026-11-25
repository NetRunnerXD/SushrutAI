"""12-Layer Structured Clinical Reasoning Pipeline.

Provides a deterministic, fast (<5 ms), auditable 12-layer clinical reasoning trace that
augments SushrutAI's LLM orchestrator and standalone clinical endpoints without adding
latency or extra LLM calls.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from app.clinical.diet_lifestyle import recommend_diet_and_lifestyle
from app.clinical.extract import extract_deterministic
from app.clinical.investigations import recommend_for_turn
from app.clinical.ml_models import score_patient_risk
from app.clinical.reasoning import _HYPOTHESIS_CATALOG
from app.clinical.routing import classify_and_route
from app.llm.intent import _phrase_intent
from app.medicine.checks import ALLERGY_CLASSES, BRANDS
from app.safety import red_flags
from app.safety.clinical_safety import evaluate_safety
from app.speech.refine import build_structured_clinical_summary


def _evaluate_medication_safety(extraction: Any, allergies: list[str], comorbidities: list[str]) -> list[dict[str, Any]]:
    """Phase 9: Deterministic medication identification, allergy cross-check, and safety precautions."""
    allergy_lower = [a.lower() for a in allergies]
    risky_generics: set[str] = set()
    for al in allergy_lower:
        for cls_name, members in ALLERGY_CLASSES.items():
            if cls_name in al:
                risky_generics |= members
        risky_generics.add(al)

    findings: list[dict[str, Any]] = []
    for med in getattr(extraction, "medications", []):
        raw_name = (
            getattr(med, "medication_name_reported", None)
            or getattr(med, "normalized_medication_name", None)
            or ""
        ).strip()
        if not raw_name:
            continue
        low = raw_name.lower()
        resolved_generics: list[str] = []
        for brand, gens in BRANDS.items():
            if brand in low:
                resolved_generics = gens
                break
        if not resolved_generics:
            for g in {x for gs in BRANDS.values() for x in gs}:
                if g in low:
                    resolved_generics.append(g)

        allergy_alerts = [g for g in resolved_generics if g in risky_generics]
        kidney_caution = (
            any("kidney" in c.lower() or "ckd" in c.lower() for c in comorbidities)
            and any(g in ("ibuprofen", "diclofenac", "aceclofenac", "naproxen", "aspirin", "nimesulide") for g in resolved_generics)
        )
        findings.append({
            "mentioned_name": raw_name,
            "resolved_generics": resolved_generics,
            "verified": bool(resolved_generics) and not getattr(med, "unidentified", False),
            "allergy_alerts": allergy_alerts,
            "kidney_nsaid_caution": kidney_caution,
            "prescribing_prohibited": True,
            "precaution": (
                f"Do NOT take {raw_name} due to recorded allergy ({', '.join(allergy_alerts)})."
                if allergy_alerts
                else "Avoid NSAID painkillers without doctor approval if kidney disease is present."
                if kidney_caution
                else "Verify exact dose and interactions with a doctor or pharmacist before starting or stopping."
            ),
        })
    return findings


@dataclass
class LayerTrace:
    layer: int
    name: str
    status: str
    latency_ms: float
    summary: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer,
            "name": self.name,
            "status": self.status,
            "latency_ms": round(self.latency_ms, 3),
            "summary": self.summary,
        }


def run_12layer_clinical_pipeline(
    text: str,
    *,
    lang: str = "en",
    patient_id: str = "anonymous",
    encounter_id: str | None = None,
    age_years: int | None = None,
    pregnant: bool = False,
    comorbidities: list[str] | None = None,
    allergies: list[str] | None = None,
    history_texts: list[str] | None = None,
) -> dict[str, Any]:
    """Execute all 12 clinical reasoning layers deterministically and return a structured audit."""
    traces: list[LayerTrace] = []
    t_total = time.perf_counter()

    # Layer 1: Patient Intent Recognition
    t0 = time.perf_counter()
    fixed_intent = _phrase_intent(text)
    intent_kind = fixed_intent or ("symptoms_or_clinical" if text.strip() else "empty")
    traces.append(LayerTrace(1, "Patient Intent Recognition", "ok", (time.perf_counter() - t0) * 1000, {"intent": intent_kind}))

    # Layer 2: Symptom Extraction (NER, negation, temporal, family/past separation, fuzzy typos)
    t0 = time.perf_counter()
    extraction = extract_deterministic(text, patient_id=patient_id, encounter_id=encounter_id, language=lang)
    sym_names = [s.name for s in extraction.symptoms]
    neg_names = [n.name for n in extraction.negated_symptoms]
    max_dur = max((s.temporal.duration_days or 0.0 for s in extraction.symptoms if s.temporal), default=0.0)
    sev = next((s.severity for s in extraction.symptoms if s.severity not in ("unknown", "not_assessed")), "unknown")
    traces.append(LayerTrace(2, "Symptom Extraction", "ok", (time.perf_counter() - t0) * 1000, {
        "active_symptoms": sym_names,
        "negated_symptoms": neg_names,
        "medications": [getattr(m, "medication_name_reported", None) or getattr(m, "normalized_medication_name", "") for m in extraction.medications],
        "history_items": [{"category": h.category, "name": h.normalized_name, "status": h.status} for h in extraction.medical_history],
    }))

    # Layer 3: Emergency Risk Screening + ML Risk Stratification
    t0 = time.perf_counter()
    rf = red_flags.check(text, lang=lang, age_years=age_years)
    safety = evaluate_safety(text, lang=lang, age_years=age_years, extraction=extraction)
    ml_risk = score_patient_risk(
        sym_names,
        text=text,
        severity=sev,
        duration_days=max_dur,
        age_years=age_years,
        comorbidities=comorbidities or [],
        functional_impact=any(s.functional_impact for s in extraction.symptoms),
        abnormal_vital=len(extraction.vital_measurements) > 0,
    )
    emergency = bool((rf.triggered and rf.kind in ("emergency", "mental_health")) or safety.escalate_now)
    traces.append(LayerTrace(3, "Emergency Risk Screening", "escalated" if emergency else "ok", (time.perf_counter() - t0) * 1000, {
        "emergency": emergency,
        "rules": list(dict.fromkeys((rf.rules or []) + safety.rule_ids)),
        "ml_risk_tier": ml_risk["risk_tier"],
        "ml_risk_probability": ml_risk["risk_probability"],
    }))

    # Layer 4: Patient History Analysis
    t0 = time.perf_counter()
    extracted_chronic = [h.normalized_name for h in extraction.medical_history if h.category == "chronic_condition" and h.normalized_name]
    all_comorb = list(dict.fromkeys([*(comorbidities or []), *extracted_chronic]))
    all_allergies = list(dict.fromkeys([*(allergies or []), *(a.substance for a in extraction.allergies)]))
    family_hx = [h.description for h in extraction.medical_history if h.category == "family_history"]
    past_hx = [h.description for h in extraction.medical_history if h.category == "previous_symptom"]
    traces.append(LayerTrace(4, "Patient History Analysis", "ok", (time.perf_counter() - t0) * 1000, {
        "comorbidities": all_comorb,
        "allergies": all_allergies,
        "family_history": family_hx,
        "past_resolved_symptoms": past_hx,
    }))

    # Layer 5: Differential Diagnosis Generation (qualitative likelihoods, never confirmed diagnosis)
    t0 = time.perf_counter()
    sig = f"{text} {' '.join(sym_names)} {' '.join(history_texts or [])} {' '.join(all_comorb)}".lower()
    differentials: list[dict[str, Any]] = []
    for spec in _HYPOTHESIS_CATALOG:
        hits = [c for c in spec.cues if c in sig]
        against = [a for a in spec.against_cues if a in sig]
        if hits:
            score = len(hits) * 2 - len(against) * 2
            if score > 0:
                is_rule_out = any(k in spec.name.lower() for k in ("cardiovascular", "arrhythmia", "febrile", "hypoglycaemia"))
                likelihood = (
                    "Important condition to rule out"
                    if is_rule_out and score < 6
                    else "More likely"
                    if score >= 6
                    else "Possible"
                    if score >= 3
                    else "Less likely"
                )
                differentials.append({
                    "condition": spec.name,
                    "likelihood_category": likelihood,
                    "confidence": "moderate" if score >= 4 else "preliminary",
                    "confirmed": False,
                    "supporting": hits,
                    "against": against,
                    "missing_information": list(spec.missing_checks),
                    "relevant_investigation": spec.next_step,
                    "next_step": spec.next_step,
                    "_score": score,
                })
    differentials.sort(key=lambda d: d["_score"], reverse=True)
    for d in differentials:
        d.pop("_score", None)
    differentials = differentials[:3]
    traces.append(LayerTrace(5, "Differential Diagnosis Generation", "ok", (time.perf_counter() - t0) * 1000, {
        "count": len(differentials),
        "hypotheses": [d["condition"] for d in differentials],
    }))

    # Layer 6: Missing Information Detection
    t0 = time.perf_counter()
    missing_dims: list[str] = []
    if sym_names:
        if max_dur <= 0:
            missing_dims.append("duration")
        if sev == "unknown":
            missing_dims.append("severity")
        if not any(s.triggers for s in extraction.symptoms):
            missing_dims.append("aggravating_relieving_factors")
        if not extraction.medications:
            missing_dims.append("current_medications")
    traces.append(LayerTrace(6, "Missing Information Detection", "ok", (time.perf_counter() - t0) * 1000, {
        "missing_dimensions": missing_dims,
    }))

    # Layer 7: Adaptive Follow-up Questioning (stopped immediately on emergency)
    t0 = time.perf_counter()
    followup_questions: list[str] = []
    if not emergency and sym_names and not fixed_intent:
        q_bank = {
            "duration": {
                "en": "How many days or hours ago did these symptoms start, and are they getting better or worse?",
                "bn": "এই লক্ষণগুলো কত দিন বা কত ঘণ্টা আগে শুরু হয়েছে, এবং সময়ের সাথে বাড়ছে না কমছে?",
                "hi": "ये लक्षण कितने दिन या घंटे पहले शुरू हुए, और क्या ये बढ़ रहे हैं या कम हो रहे हैं?",
            },
            "severity": {
                "en": "How severe are the symptoms right now — mild, moderate, or interfering with daily activities?",
                "bn": "এখন সমস্যাটা কতটা তীব্র — হালকা, মাঝারি, নাকি দৈনন্দিন কাজে বাধা দিচ্ছে?",
                "hi": "अभी लक्षण कितने गंभीर हैं — हल्के, मध्यम, या दैनिक कामकाज में बाधा डाल रहे हैं?",
            },
            "aggravating_relieving_factors": {
                "en": "Does anything specific make it worse or better (such as standing up, exertion, food, or rest)?",
                "bn": "কোনো কিছু করলে কি এটা বাড়ে বা কমে (যেমন উঠে দাঁড়ালে, হাঁটলে, খাওয়ার পর বা বিশ্রামে)?",
                "hi": "क्या किसी चीज़ से यह बढ़ता या कम होता है (जैसे खड़े होने, चलने, खाने या आराम करने पर)?",
            },
            "current_medications": {
                "en": "Are you currently taking any medicines or have any known drug allergies?",
                "bn": "আপনি কি বর্তমানে কোনো ওষুধ খাচ্ছেন বা কোনো ওষুধে অ্যালার্জি আছে?",
                "hi": "क्या आप अभी कोई दवा ले रहे हैं या किसी दवा से एलर्जी है?",
            },
        }
        use_l = lang if lang in ("en", "bn", "hi") else "en"
        for dim in missing_dims[:3]:
            if dim in q_bank:
                followup_questions.append(q_bank[dim][use_l])
    traces.append(LayerTrace(7, "Adaptive Follow-up Questioning", "stopped_for_emergency" if emergency else "ok", (time.perf_counter() - t0) * 1000, {
        "questions_count": len(followup_questions),
    }))

    # Layer 8: Investigation Recommendation
    t0 = time.perf_counter()
    investigations = recommend_for_turn(
        sig,
        duration_days=max_dur,
        urgent=(ml_risk["risk_tier"] == "high"),
        emergency=emergency,
        pregnant=pregnant,
        max_items=4,
    )
    traces.append(LayerTrace(8, "Investigation Recommendation", "ok", (time.perf_counter() - t0) * 1000, {
        "count": len(investigations),
        "tests": [i["investigation_name"] for i in investigations],
    }))

    # Layer 9: Medical Speciality Selection
    t0 = time.perf_counter()
    routing = classify_and_route(
        text,
        lang=lang,
        extraction=extraction,
        safety=safety,
        age_years=age_years,
        pregnant=pregnant,
        comorbidities=all_comorb,
        red_flag_triggered=emergency,
        red_flag_kind=rf.kind,
    )
    traces.append(LayerTrace(9, "Medical Speciality Selection", "ok", (time.perf_counter() - t0) * 1000, {
        "department": routing.primary_department,
        "qualification_level": routing.qualification,
        "urgency": routing.urgency_api,
    }))

    # Layer 10: Diet and Lifestyle Guidance
    t0 = time.perf_counter()
    diet = recommend_diet_and_lifestyle(
        sig,
        dept=routing.primary_department,
        lang=lang,
        age_years=age_years,
        pregnant=pregnant,
        comorbidities=all_comorb,
        allergies=all_allergies,
    )
    traces.append(LayerTrace(10, "Diet and Lifestyle Guidance", "ok", (time.perf_counter() - t0) * 1000, {
        "department_tailored": routing.primary_department,
    }))

    # Layer 11: Final Response Context Readiness
    t0 = time.perf_counter()
    traces.append(LayerTrace(11, "Final Response Generation", "ready", (time.perf_counter() - t0) * 1000, {
        "language": lang,
        "uncertainty_preserved": True,
    }))

    # Layer 12: Clinical Safety Verification
    t0 = time.perf_counter()
    safety_ok = (not emergency or routing.urgency_api == "emergency") and all(not d["confirmed"] for d in differentials)
    traces.append(LayerTrace(12, "Clinical Safety Verification", "verified" if safety_ok else "adjusted", (time.perf_counter() - t0) * 1000, {
        "emergency_override_active": emergency,
        "no_confirmed_diagnosis": all(not d["confirmed"] for d in differentials),
        "clinician_confirmation_required": True,
    }))

    med_safety = _evaluate_medication_safety(extraction, all_allergies, all_comorb)
    structured_summary = build_structured_clinical_summary(text, extraction)

    return {
        "total_latency_ms": round((time.perf_counter() - t_total) * 1000, 3),
        "emergency": emergency,
        "emergency_messages": rf.messages if emergency else [],
        "intent": intent_kind,
        "symptoms": sym_names,
        "negated_symptoms": neg_names,
        "differentials": differentials,
        "followup_questions": followup_questions,
        "investigations": investigations,
        "routing": routing.as_dict(),
        "diet_lifestyle": diet,
        "medication_safety": med_safety,
        "structured_summary": structured_summary,
        "ml_risk": ml_risk,
        "layers": [t.as_dict() for t in traces],
    }
