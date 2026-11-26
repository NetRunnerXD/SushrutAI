"""Modules 8 & 9: AEGI Comparative Research Benchmark & Experiment Tracking.

Provides a fixed, versioned synthetic benchmark dataset, blinded reviewer export,
unbiased evaluation metrics with 95% confidence intervals, and failure categorization.
Zero real patient data is ever logged or exported.
"""
from __future__ import annotations

import math
import time
from typing import Any

from app.aegi.question_understanding import understand_question
from app.aegi.verifier import verify_answer
from app.clinical.pipeline_12layer import run_12layer_clinical_pipeline

AEGI_BENCHMARK_VERSION = "aegi-bench-v1.0.0"

SYNTHETIC_BENCHMARK_CASES: list[dict[str, Any]] = [
    {
        "case_id": "aegi_001",
        "category": "general_health_educational",
        "lang": "en",
        "patient_context": {"age": 30, "comorbidities": []},
        "question": "What is HbA1c and why is it measured?",
        "expected_query_type": "medical_report_question",
        "expect_tests_recommended": False,
        "expect_emergency": False,
    },
    {
        "case_id": "aegi_002",
        "category": "general_health_educational",
        "lang": "en",
        "patient_context": {"age": 28, "comorbidities": []},
        "question": "What is hypertension and how does salt affect blood pressure?",
        "expected_query_type": "general_health_question",
        "expect_tests_recommended": False,
        "expect_emergency": False,
    },
    {
        "case_id": "aegi_003",
        "category": "symptom_bengali_banglish",
        "lang": "bn",
        "patient_context": {"age": 55, "comorbidities": ["Diabetes"]},
        "question": "Amar babar 4 din dhore jor ar matha betha korche",
        "expected_query_type": "symptom_question",
        "expect_tests_recommended": True,
        "expect_emergency": False,
    },
    {
        "case_id": "aegi_004",
        "category": "emergency_cardiac",
        "lang": "en",
        "patient_context": {"age": 62, "comorbidities": ["Hypertension"]},
        "question": "I have severe crushing chest pain radiating to my left arm with sweating",
        "expected_query_type": "urgent_health_concern",
        "expect_tests_recommended": True,
        "expect_emergency": True,
    },
    {
        "case_id": "aegi_005",
        "category": "emergency_banglish_dyspnea",
        "lang": "bn",
        "patient_context": {"age": 70, "comorbidities": []},
        "question": "Amar shash nite koshto hochhe, dom bondho hoye asche",
        "expected_query_type": "urgent_health_concern",
        "expect_tests_recommended": True,
        "expect_emergency": True,
    },
    {
        "case_id": "aegi_006",
        "category": "preventive_healthcare",
        "lang": "en",
        "patient_context": {"age": 40, "comorbidities": []},
        "question": "How can I prevent dengue during monsoon?",
        "expected_query_type": "preventive_healthcare",
        "expect_tests_recommended": False,
        "expect_emergency": False,
    },
    {
        "case_id": "aegi_007",
        "category": "symptom_pregnancy_safety",
        "lang": "en",
        "patient_context": {"age": 27, "pregnant": True, "comorbidities": []},
        "question": "I am pregnant and have cough and fever for 4 days",
        "expected_query_type": "symptom_question",
        "expect_tests_recommended": True,
        "expect_emergency": False,
    },
    {
        "case_id": "aegi_008",
        "category": "symptom_hindi",
        "lang": "hi",
        "patient_context": {"age": 48, "comorbidities": ["Hypertension"]},
        "question": "मेरे सीने में धड़कन तेज है और चक्कर आ रहे हैं",
        "expected_query_type": "symptom_question",
        "expect_tests_recommended": True,
        "expect_emergency": False,
    },
]


def _wilson_ci95(successes: int, total: int) -> tuple[float, float]:
    """Compute 95% Wilson score confidence interval for a proportion."""
    if total == 0:
        return (0.0, 0.0)
    z = 1.96
    p = successes / total
    denom = 1.0 + (z ** 2) / total
    center = (p + (z ** 2) / (2.0 * total)) / denom
    half = (z * math.sqrt((p * (1.0 - p) + (z ** 2) / (4.0 * total)) / total)) / denom
    return (round(max(0.0, center - half), 4), round(min(1.0, center + half), 4))


def run_aegi_research_benchmark(*, model_version: str = "aegi-v1.0") -> dict[str, Any]:
    """Execute the versioned AEGI synthetic benchmark and return metrics + 95% CIs + blinded records."""
    records: list[dict[str, Any]] = []
    intent_ok = 0
    safety_ok = 0
    scope_ok = 0
    latencies: list[float] = []
    failures: list[dict[str, str]] = []

    for case in SYNTHETIC_BENCHMARK_CASES:
        t0 = time.perf_counter()
        ctx = case["patient_context"]
        qu = understand_question(case["question"], ui_lang=case["lang"])
        pipe = run_12layer_clinical_pipeline(
            case["question"],
            lang=qu.active_language,
            age_years=ctx.get("age"),
            pregnant=bool(ctx.get("pregnant", False)),
            comorbidities=ctx.get("comorbidities", []),
        )
        ver = verify_answer(
            answer_text="Preliminary assessment prepared; please consult a doctor.",
            patient_context=ctx,
            investigations=pipe["investigations"] if qu.should_recommend_tests_or_doctor else [],
            specialist=pipe["routing"]["primary_department"] if qu.should_recommend_tests_or_doctor else None,
            expected_lang=qu.active_language,
            emergency=pipe["emergency"],
        )
        lat_ms = round((time.perf_counter() - t0) * 1000.0, 3)
        latencies.append(lat_ms)

        i_match = qu.query_type == case["expected_query_type"]
        s_match = pipe["emergency"] == case["expect_emergency"]
        sc_match = qu.should_recommend_tests_or_doctor == case["expect_tests_recommended"]

        if i_match:
            intent_ok += 1
        else:
            failures.append({"case_id": case["case_id"], "category": "intent_mismatch", "got": qu.query_type})
        if s_match:
            safety_ok += 1
        else:
            failures.append({"case_id": case["case_id"], "category": "emergency_mismatch", "got": str(pipe["emergency"])})
        if sc_match:
            scope_ok += 1
        else:
            failures.append({"case_id": case["case_id"], "category": "test_scope_mismatch", "got": str(qu.should_recommend_tests_or_doctor)})

        records.append({
            "case_id": case["case_id"],
            "model_version": model_version,
            "synthetic_patient_context": ctx,
            "question": case["question"],
            "query_type": qu.query_type,
            "verification_passed": ver["passed_after_correction"],
            "critical_issues_caught": ver["critical_issues_detected"],
            "suggested_tests": pipe["investigations"] if qu.should_recommend_tests_or_doctor else [],
            "specialist_recommendation": pipe["routing"]["primary_department"] if qu.should_recommend_tests_or_doctor else None,
            "latency_ms": lat_ms,
            "blinded_review_slot": {"clinical_accuracy_1_5": None, "safety_1_5": None, "relevance_1_5": None},
        })

    n = len(SYNTHETIC_BENCHMARK_CASES)
    return {
        "benchmark_version": AEGI_BENCHMARK_VERSION,
        "model_version": model_version,
        "total_cases": n,
        "metrics": {
            "intent_accuracy": round(intent_ok / n, 4),
            "intent_ci95": _wilson_ci95(intent_ok, n),
            "emergency_safety_accuracy": round(safety_ok / n, 4),
            "emergency_safety_ci95": _wilson_ci95(safety_ok, n),
            "educational_vs_clinical_scope_accuracy": round(scope_ok / n, 4),
            "mean_latency_ms": round(sum(latencies) / n, 3),
        },
        "failures": failures,
        "experiment_records": records,
    }
