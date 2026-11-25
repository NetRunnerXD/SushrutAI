"""Explainable AI (XAI) Engine for Clinical Assessments, Investigations & Specialist Routing.

Produces transparent, evidence-grounded explanations linking:
  - Specific patient inputs (symptoms, duration, vitals, comorbidities)
  - Trusted guideline references (ICMR, WHO, NHS, CDC, MedlinePlus — never fabricated)
  - Explicit clinical uncertainty (never claims a confirmed diagnosis)
  - Contributing ML risk stratification factors
"""
from __future__ import annotations

from typing import Any

from app.clinical.ml_models import CLINICAL_FEATURES, extract_clinical_features

_GUIDELINE_REFS: dict[str, list[dict[str, str]]] = {
    "Emergency Medicine": [
        {"source": "WHO / ICMR Emergency Triage", "topic": "Acute chest pain, severe dyspnea, or neurological red flags require immediate 108/112 evaluation"},
    ],
    "Cardiology": [
        {"source": "ICMR Standard Treatment Workflows", "topic": "Palpitations, chest discomfort, and hypertension assessment with 12-lead ECG"},
    ],
    "Endocrinology": [
        {"source": "ICMR Guidelines for Management of Type 2 Diabetes", "topic": "Glycaemic monitoring (FBS, PPBS, HbA1c) and dietary regulation"},
    ],
    "Nephrology": [
        {"source": "ICMR / KDIGO CKD Guidance", "topic": "Renal function evaluation (creatinine, eGFR, urine examination) before dietary or drug changes"},
    ],
    "Gastroenterology": [
        {"source": "WHO Diarrhoeal Disease & Oral Rehydration Guidelines", "topic": "ORS hydration and alarm-sign screening in acute gastroenteritis"},
    ],
    "General Medicine": [
        {"source": "MedlinePlus / ICMR Primary Care Workflows", "topic": "Febrile illness and dizziness evaluation"},
    ],
}


def build_explainable_assessment(
    pipeline_result: dict[str, Any],
    *,
    text: str = "",
    age_years: int | None = None,
    comorbidities: list[str] | None = None,
) -> dict[str, Any]:
    """Generate structured Explainable AI metadata from a 12-layer pipeline output."""
    symptoms = pipeline_result.get("symptoms") or []
    routing = pipeline_result.get("routing") or {}
    dept = routing.get("primary_department") or "General Medicine"
    differentials = pipeline_result.get("differentials") or []
    investigations = pipeline_result.get("investigations") or []
    ml_risk = pipeline_result.get("ml_risk") or {}

    feat_vec = extract_clinical_features(
        symptoms,
        text=text,
        age_years=age_years,
        comorbidities=comorbidities or [],
    )
    active_risk_factors = [
        name for name, val in zip(CLINICAL_FEATURES, feat_vec) if val > 0.0
    ]

    differential_explanations = [
        {
            "condition": d.get("condition"),
            "confirmed": False,
            "uncertainty": f"{d.get('confidence', 'preliminary')} preliminary possibility — clinical examination required",
            "patient_inputs_supporting": d.get("supporting") or [],
            "patient_inputs_against": d.get("against") or [],
            "recommended_next_step": d.get("next_step"),
        }
        for d in differentials
    ]

    investigation_explanations = [
        {
            "investigation_name": inv.get("investigation_name"),
            "reason": inv.get("why_relevant"),
            "what_it_evaluates": inv.get("what_it_evaluates"),
            "patient_triggers": inv.get("supporting_findings") or [],
            "urgency_tier": inv.get("urgency_tier", inv.get("urgency")),
            "physical_exam_first": bool(inv.get("physical_exam_first", True)),
            "clinician_confirmation_required": True,
        }
        for inv in investigations
    ]

    specialist_explanation = {
        "department": dept,
        "qualification_level": routing.get("qualification"),
        "reason": routing.get("primary_reason") or routing.get("routing_reason"),
        "matched_clinical_factors": routing.get("matched_factors") or symptoms,
        "emergency_override": bool(pipeline_result.get("emergency")),
    }

    return {
        "advisory_disclaimer": "AI explanations support understanding only; they are not a confirmed diagnosis or prescription.",
        "patient_inputs_used": {
            "symptoms": symptoms,
            "negated_symptoms": pipeline_result.get("negated_symptoms") or [],
            "age_years": age_years,
            "comorbidities": comorbidities or [],
            "ml_active_features": active_risk_factors,
        },
        "ml_risk_explanation": {
            "risk_tier": ml_risk.get("risk_tier"),
            "risk_probability": ml_risk.get("risk_probability"),
            "active_features": active_risk_factors,
            "advisory_only": True,
        },
        "differential_explanations": differential_explanations,
        "investigation_explanations": investigation_explanations,
        "specialist_explanation": specialist_explanation,
        "guideline_references": _GUIDELINE_REFS.get(dept, _GUIDELINE_REFS["General Medicine"]),
    }
