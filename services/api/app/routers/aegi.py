"""AEGI API Router: Adaptive Evidence-Grounded Intelligence (Modules 1-9)."""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.aegi.benchmark import SYNTHETIC_BENCHMARK_CASES, run_aegi_research_benchmark
from app.aegi.interview_and_grounding import build_interview_record, retrieve_curated_evidence
from app.aegi.question_understanding import understand_question
from app.aegi.test_intelligence import recommend_tests_with_redundancy_check
from app.aegi.verifier import verify_answer
from app.clinical.family_memory import get_member_current_user, get_member_memory_summary, resolve_family_member
from app.clinical.pipeline_12layer import run_12layer_clinical_pipeline
from app.db import get_db, scope_to
from app.models import AuditEvent
from app.security.auth import CurrentUser, current_user
from app.security.consent import active_purposes

router = APIRouter(prefix="/aegi", tags=["aegi"])


class AegiAskIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    lang: str = "en"
    locked_lang: str | None = None
    member_id: str | None = None
    history: list[dict[str, str]] = Field(default_factory=list)


@router.post("/ask")
async def aegi_ask(
    body: AegiAskIn,
    cu: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Run the full 9-module AEGI pipeline with independent verification and provenance."""
    if "core_service" not in await active_purposes(db, cu.account_id):
        raise HTTPException(403, "Consent 'core_service' is required")
    t0 = time.perf_counter()

    # Module 1: Adaptive Question Understanding
    qu = understand_question(
        body.text,
        locked_lang=body.locked_lang,
        ui_lang=body.lang,
        history=body.history,
    )

    # Module 6: Patient Context Consistency (member-isolated)
    target_cu = cu
    family_ctx: dict[str, Any] | None = None
    if body.member_id:
        mcu = await get_member_current_user(db, cu, body.member_id)
        if mcu is None:
            raise HTTPException(404, "Family member not found")
        target_cu = mcu
        family_ctx = await get_member_memory_summary(db, target_cu, relation="family")
    else:
        fam = await resolve_family_member(db, cu, body.text, lang=qu.active_language, auto_create_if_missing=False)
        if fam.matched and fam.member_id:
            mcu = await get_member_current_user(db, cu, fam.member_id)
            if mcu is not None:
                target_cu = mcu
                family_ctx = await get_member_memory_summary(
                    db, target_cu, name=fam.member_name or "", relation=fam.relation or ""
                )

    age_years = family_ctx.get("age") if family_ctx else None
    comorbidities = family_ctx.get("conditions", []) if family_ctx else []
    allergies = family_ctx.get("allergies", []) if family_ctx else []

    # Module 3: Evidence-Grounded Retrieval (RAG + GNN/KG)
    evidence = await retrieve_curated_evidence(db, body.text, lang=qu.active_language)

    # 12-Layer Clinical Pipeline
    pipe = run_12layer_clinical_pipeline(
        body.text,
        lang=qu.active_language,
        patient_id=target_cu.id,
        age_years=age_years,
        comorbidities=comorbidities,
        allergies=allergies,
        history_texts=[h.get("content", "") for h in body.history],
    )

    # Module 2: Adaptive Interview Record
    interview = build_interview_record(
        current_text=body.text,
        candidate_questions=pipe.get("followup_questions") or [],
        history=body.history,
        missing_dimensions=(pipe["layers"][5]["summary"].get("missing_dimensions") if len(pipe["layers"]) > 5 else []),
        emergency=pipe["emergency"],
    )

    # Module 5: Test Necessity & Redundancy Intelligence (ONLY for personal clinical assessments!)
    tests = (
        await recommend_tests_with_redundancy_check(
            db,
            target_cu,
            signal_text=body.text + " " + " ".join(pipe.get("symptoms") or []),
            symptoms=pipe.get("symptoms") or [],
            urgent=(pipe["ml_risk"]["risk_tier"] == "high"),
            emergency=pipe["emergency"],
        )
        if qu.should_recommend_tests_or_doctor
        else []
    )

    # Module 7: Intelligent Response Quality Optimizer
    if pipe["emergency"]:
        draft_reply = " ".join(pipe.get("emergency_messages") or ["Call 108 / 112 immediately."])
        response_structure = {"mode": "emergency_escalation"}
    elif qu.is_educational:
        top_excerpts = " ".join(e["excerpt"] for e in evidence["evidence_items"][:2] if e.get("excerpt")).strip()
        draft_reply = top_excerpts or (
            "Here is general evidence-based health information for your question. "
            "Consult a qualified healthcare professional for personal medical advice."
        )
        response_structure = {"mode": "educational_direct_answer"}
    else:
        diffs = ", ".join(d["condition"] for d in pipe.get("differentials") or []) or "multiple non-specific causes"
        spec = pipe["routing"]["primary_department"]
        draft_reply = (
            f"Based on your reported symptoms ({', '.join(pipe.get('symptoms') or ['general symptoms'])}), "
            f"possible explanations to discuss with a doctor include {diffs}. "
            f"Recommended specialist category: {spec}."
        )
        response_structure = {
            "mode": "structured_clinical_assessment",
            "sections": {
                "understanding": pipe.get("symptoms") or [],
                "possible_explanations": pipe.get("differentials") or [],
                "warning_signs": pipe.get("emergency_messages") or [],
                "follow_up_questions": interview["selected_questions"],
                "possible_tests": tests,
                "specialist_category": pipe["routing"],
                "safe_next_steps": pipe["diet_lifestyle"],
            },
        }

    # Module 4: Independent Answer Verification Engine
    verification = verify_answer(
        answer_text=draft_reply,
        citations=[{"title": e["source_title"]} for e in evidence["evidence_items"]],
        retrieved_evidence=evidence["evidence_items"],
        patient_context={"age": age_years, "comorbidities": comorbidities, "allergies": allergies},
        investigations=tests,
        specialist=pipe["routing"]["primary_department"] if qu.should_recommend_tests_or_doctor else None,
        expected_lang=qu.active_language,
        emergency=pipe["emergency"],
    )

    await scope_to(db, target_cu.id, cu.account_id)
    db.add(AuditEvent(
        user_id=target_cu.id,
        actor_id=cu.account_id,
        actor_role="patient",
        action="aegi_ask_completed",
        entity_type="aegi_turn",
        entity_id=qu.query_type[:36],
        detail_enc=None,
    ))
    await db.commit()
    await scope_to(db, cu.id, cu.account_id)

    return {
        "reply": verification["corrected_answer"],
        "language": qu.active_language,
        "bcp47": qu.bcp47,
        "question_understanding": qu.as_dict(),
        "family_context": family_ctx,
        "interview": interview,
        "evidence": evidence,
        "tests": verification["filtered_investigations"],
        "routing": pipe["routing"] if qu.should_recommend_tests_or_doctor else None,
        "response_structure": response_structure,
        "verification": verification,
        "latency_ms": round((time.perf_counter() - t0) * 1000.0, 3),
    }


class VerifyIn(BaseModel):
    answer_text: str = Field(min_length=1, max_length=8000)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    retrieved_evidence: list[dict[str, Any]] = Field(default_factory=list)
    patient_context: dict[str, Any] = Field(default_factory=dict)
    investigations: list[dict[str, Any]] = Field(default_factory=list)
    specialist: str | None = None
    expected_lang: str = "en"
    emergency: bool = False


@router.post("/verify")
async def aegi_verify(body: VerifyIn, cu: CurrentUser = Depends(current_user)):
    """Standalone Independent Answer Verification endpoint."""
    return verify_answer(
        answer_text=body.answer_text,
        citations=body.citations,
        retrieved_evidence=body.retrieved_evidence,
        patient_context=body.patient_context,
        investigations=body.investigations,
        specialist=body.specialist,
        expected_lang=body.expected_lang,
        emergency=body.emergency,
    )


@router.get("/benchmark/dataset")
async def aegi_benchmark_dataset(cu: CurrentUser = Depends(current_user)):
    """Return the versioned synthetic AEGI benchmark dataset."""
    return {"cases": SYNTHETIC_BENCHMARK_CASES, "count": len(SYNTHETIC_BENCHMARK_CASES)}


@router.post("/benchmark/run")
async def aegi_benchmark_run(cu: CurrentUser = Depends(current_user)):
    """Execute the reproducible AEGI comparative research benchmark."""
    return run_aegi_research_benchmark()
