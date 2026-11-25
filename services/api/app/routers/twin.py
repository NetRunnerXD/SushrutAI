"""API contracts for Digital Twin, Explainable AI, Offline-First Sync, Lab Intelligence, and Proactive Follow-Up."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.clinical.conversation_memory import (
    generate_medical_history_pdf,
    list_conversation_summaries,
    save_conversation_summary,
)
from app.clinical.digital_twin import build_digital_twin
from app.clinical.explainable_ai import build_explainable_assessment
from app.clinical.family_memory import get_member_current_user
from app.clinical.labs import parse_lab_report_structured
from app.clinical.offline_sync import process_offline_sync_batch
from app.clinical.pipeline_12layer import run_12layer_clinical_pipeline
from app.clinical.proactive_agent import acknowledge_followup_item, build_proactive_followup_plan
from app.db import get_db, scope_to
from app.security.auth import CurrentUser, current_user
from app.security.consent import active_purposes

router = APIRouter(tags=["digital-twin-and-intelligence"])


async def _resolve_target_cu(db: AsyncSession, cu: CurrentUser, member_id: str | None) -> CurrentUser:
    if not member_id:
        return cu
    mcu = await get_member_current_user(db, cu, member_id)
    if mcu is None:
        raise HTTPException(404, "Family member not found")
    return mcu


@router.get("/twin")
async def get_digital_twin(
    member_id: str | None = Query(default=None),
    cu: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve the longitudinal Personal Health Digital Twin (self or family member) with FHIR R4 bundle."""
    if "core_service" not in await active_purposes(db, cu.account_id):
        raise HTTPException(403, "Consent 'core_service' is required")
    target = await _resolve_target_cu(db, cu, member_id)
    out = await build_digital_twin(db, target, relation="family" if member_id else "self")
    await scope_to(db, cu.id, cu.account_id)
    return out


class ExplainIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    lang: str = "en"
    age_years: int | None = None
    comorbidities: list[str] = Field(default_factory=list)
    allergies: list[str] = Field(default_factory=list)


@router.post("/twin/explain")
async def explain_assessment(
    body: ExplainIn,
    cu: CurrentUser = Depends(current_user),
):
    """Return Explainable AI breakdown for symptom assessment, investigations, and specialist routing."""
    res = run_12layer_clinical_pipeline(
        body.text,
        lang=body.lang,
        patient_id=cu.id,
        age_years=body.age_years,
        comorbidities=body.comorbidities,
        allergies=body.allergies,
    )
    xai = build_explainable_assessment(
        res,
        text=body.text,
        age_years=body.age_years,
        comorbidities=body.comorbidities,
    )
    return {"pipeline": res, "explainability": xai}


class OfflineSyncIn(BaseModel):
    mutations: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    member_id: str | None = None


@router.post("/offline/sync")
async def offline_sync(
    body: OfflineSyncIn,
    cu: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Synchronize queued offline mutations with deterministic conflict resolution."""
    if "core_service" not in await active_purposes(db, cu.account_id):
        raise HTTPException(403, "Consent 'core_service' is required")
    target = await _resolve_target_cu(db, cu, body.member_id)
    out = await process_offline_sync_batch(db, target, body.mutations)
    await scope_to(db, cu.id, cu.account_id)
    return out


@router.get("/followup/plan")
async def get_followup_plan(
    lang: str = Query(default="en"),
    member_id: str | None = Query(default=None),
    cu: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve proactive follow-up reminders, pending reports, and consultation follow-ups."""
    target = await _resolve_target_cu(db, cu, member_id)
    out = await build_proactive_followup_plan(db, target, lang=lang)
    await scope_to(db, cu.id, cu.account_id)
    return out


class AckIn(BaseModel):
    item_id: str = Field(min_length=1, max_length=64)
    note: str = Field(default="", max_length=300)
    member_id: str | None = None


@router.post("/followup/acknowledge")
async def ack_followup(
    body: AckIn,
    cu: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Acknowledge a proactive follow-up item."""
    target = await _resolve_target_cu(db, cu, body.member_id)
    out = await acknowledge_followup_item(db, target, item_id=body.item_id, note=body.note)
    await scope_to(db, cu.id, cu.account_id)
    return out


class StructuredReportIn(BaseModel):
    ocr_text: str = Field(min_length=1, max_length=16000)
    sex: str | None = None
    age_years: float | None = None
    pregnant: bool = False


@router.post("/reports/parse-structured")
async def parse_structured_report(
    body: StructuredReportIn,
    cu: CurrentUser = Depends(current_user),
):
    """Parse lab report OCR text with LOINC mapping, report date, and uncertainty/confirmation flags."""
    return parse_lab_report_structured(
        body.ocr_text,
        sex=body.sex,
        age_years=body.age_years,
        pregnant=body.pregnant,
    )


@router.get("/memory/summaries")
async def get_conversation_summaries(
    member_id: str | None = Query(default=None),
    q: str = Query(default=""),
    cu: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """List and search encrypted automatic conversation summaries for self or a family member."""
    target = await _resolve_target_cu(db, cu, member_id)
    items = await list_conversation_summaries(db, target, query=q)
    await scope_to(db, cu.id, cu.account_id)
    return {"patient_id": target.id, "summaries": items, "count": len(items)}


class SummaryEditIn(BaseModel):
    main_complaint: str | None = Field(default=None, max_length=300)
    doctor_consultation_status: str | None = Field(default=None, max_length=64)
    member_id: str | None = None


@router.patch("/memory/summaries/{encounter_id}")
async def update_conversation_summary(
    encounter_id: str,
    body: SummaryEditIn,
    cu: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Edit an existing conversation summary (preserving provenance)."""
    import json as _json
    from app.models import Encounter

    target = await _resolve_target_cu(db, cu, body.member_id)
    await scope_to(db, target.id, cu.account_id)
    enc = await db.get(Encounter, encounter_id)
    if enc is None or enc.user_id != target.id or not enc.summary_enc:
        await scope_to(db, cu.id, cu.account_id)
        raise HTTPException(404, "Summary not found")
    data = _json.loads(target.cipher.dec(enc.summary_enc))
    if body.main_complaint is not None:
        data["main_complaint"] = body.main_complaint
    if body.doctor_consultation_status is not None:
        data["doctor_consultation_status"] = body.doctor_consultation_status
    enc.summary_enc = target.cipher.enc(_json.dumps(data, ensure_ascii=False))
    await db.commit()
    await scope_to(db, cu.id, cu.account_id)
    return data


@router.delete("/memory/summaries/{encounter_id}", status_code=204)
async def delete_conversation_summary(
    encounter_id: str,
    member_id: str | None = Query(default=None),
    cu: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a conversation summary record."""
    from app.models import Encounter

    target = await _resolve_target_cu(db, cu, member_id)
    await scope_to(db, target.id, cu.account_id)
    enc = await db.get(Encounter, encounter_id)
    if enc is None or enc.user_id != target.id:
        await scope_to(db, cu.id, cu.account_id)
        raise HTTPException(404, "Summary not found")
    enc.summary_enc = None
    await db.commit()
    await scope_to(db, cu.id, cu.account_id)


@router.get("/records/report.pdf")
async def download_medical_history_pdf(
    member_id: str | None = Query(default=None),
    cu: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Download a doctor-friendly PDF medical history report for self or an authorized family member."""
    if "core_service" not in await active_purposes(db, cu.account_id):
        raise HTTPException(403, "Consent 'core_service' is required")
    target = await _resolve_target_cu(db, cu, member_id)
    pdf = await generate_medical_history_pdf(db, target)
    await scope_to(db, cu.id, cu.account_id)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="sushrutai-medical-report-{target.id[:8]}.pdf"'},
    )
