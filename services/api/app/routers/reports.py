"""Lab-report parsing and medical-image description (local vision models only — images never leave the device)."""
from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clinical.labs import parse_lab_report
from app.config import get_settings
from app.db import get_db
from app.llm.orchestrator import LANG_NAMES
from app.llm.providers import ProviderUnavailable, ollama
from app.models import Document, LabResult, Prescription
from app.models import _now
from app.ocr.images import MODALITY_PROMPT, doc_image_b64 as _doc_image_b64
from app.ocr.pipeline import load_image
from app.routers.documents import read_document
from app.vision.screen import for_person, prompt_block, screen
from app.safety.disclaimer import disclaimer
from app.security.auth import CurrentUser, current_user
from app.security.consent import active_purposes

router = APIRouter(tags=["reports"])

LAB_PROMPT = """This is a medical laboratory report from India. Transcribe every test result line exactly as written,
one test per line, in the form:
TEST: <test name> <value> <unit> <reference range>
Copy numbers exactly. Do not add tests that are not printed. Do not interpret the results."""

SUPPORTED = {"chest_xray", "other_xray", "skin", "eye_fundus", "ct", "mri", "ultrasound", "ecg"}
# MedGemma-4B was trained on chest X-ray, dermatology, ophthalmology and histopathology images; CT/MRI/US/ECG
# single slices are described with an explicit low-confidence warning.
LOW_CONFIDENCE_MODALITIES = {"ct", "mri", "ultrasound", "ecg", "other_xray"}


class ReportIn(BaseModel):
    document_id: str
    save: bool = False  # values are saved only after the person checks them against the document (/confirm)


@router.post("/reports/parse")
async def parse_report(body: ReportIn, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    purposes = await active_purposes(db, cu.account_id)
    if "core_service" not in purposes:
        raise HTTPException(403, "Please accept the privacy notice first")
    b64, doc = await _doc_image_b64(cu, db, body.document_id, max_side=1600)
    s = get_settings()
    from app.llm.providers import doc_model_for
    from app.security.settings import get_settings_row

    model = doc_model_for((await get_settings_row(db, cu.account_id)).model_pref or "balanced", "lab_report")

    transcript = [""]

    async def read(m: str):
        r = await ollama().chat(m, [{"role": "user", "content": LAB_PROMPT}], images=[b64],
                                temperature=0.0, num_ctx=6144, num_predict=1500)
        transcript[0] = r.text
        return r
    try:
        try:
            results = parse_lab_report((await read(model)).text)
        except ProviderUnavailable:
            results = []
            if model == s.rx_vlm_model:
                raise
        if not results and model != s.rx_vlm_model:  # the faster reader failed or read nothing
            model = s.rx_vlm_model
            results = parse_lab_report((await read(model)).text)
    except ProviderUnavailable as e:
        raise HTTPException(503, e.hint) from e
    doc.extracted = [r.as_dict() for r in results]
    if body.save and "history_storage" in purposes:
        doc.reviewed_at = _now()
        for r in results:
            db.add(LabResult(user_id=cu.id, document_id=doc.id, test_name=r.test_name, loinc_code=r.loinc_code,
                             value=r.value, value_text=r.value_text, unit=r.unit, ref_low=r.ref_low,
                             ref_high=r.ref_high, flag=r.flag))
    await db.commit()
    abnormal = [r for r in results if r.flag in ("H", "L")]
    return {
        "results": [r.as_dict() for r in results],
        "abnormal": [f"{r.display}: {r.value_text} {r.unit or ''} ({'high' if r.flag == 'H' else 'low'})"
                     for r in abnormal],
        "document_id": doc.id, "model": model, "saved": body.save and "history_storage" in purposes,
        "notice": "Values were read by a machine from your report — check them against the original. "
                  "Only a doctor can interpret what they mean for you.",
        "transcript": transcript[0],
    }


@router.get("/reports/labs")
async def list_labs(loinc: str | None = None, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    q = select(LabResult).where(LabResult.user_id == cu.id)
    if loinc:
        q = q.where(LabResult.loinc_code == loinc)
    return [{"id": r.id, "test_name": r.test_name, "loinc_code": r.loinc_code, "value": r.value, "unit": r.unit,
             "ref_low": r.ref_low, "ref_high": r.ref_high, "flag": r.flag, "observed_at": r.observed_at}
            for r in await db.scalars(q.order_by(LabResult.observed_at))]


class ImageIn(BaseModel):
    document_id: str
    question: str = Field("", max_length=500)
    lang: Literal["en", "hi", "bn", "ta", "te", "mr", "gu", "kn", "ml", "or"] = "en"


@router.post("/images/analyze")
async def analyze_image(body: ImageIn, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    if "core_service" not in await active_purposes(db, cu.account_id):
        raise HTTPException(403, "Please accept the privacy notice first")
    b64, doc = await _doc_image_b64(cu, db, body.document_id, max_side=896)
    s = get_settings()
    try:
        m = await ollama().chat(s.vlm_model, [{"role": "user", "content": MODALITY_PROMPT}], images=[b64],
                                temperature=0.0, num_ctx=2048, num_predict=8)
    except ProviderUnavailable as e:
        raise HTTPException(503, e.hint) from e
    modality = re.sub(r"[^a-z_]", "", m.text.strip().lower().split()[0] if m.text.strip() else "other")
    if modality in ("lab_report", "prescription", "document"):
        return {"modality": modality, "supported": False,
                "message": "This looks like a document. Use 'Prescriptions' or lab-report upload to read it."}
    if modality not in SUPPORTED:
        return {"modality": modality, "supported": False,
                "message": "SushrutAI can only describe medical images such as X-rays or skin photos."}
    lang = LANG_NAMES.get(body.lang, "English")
    # a dedicated screening model looks first, on the original image (MedGemma gets a contrast-adjusted copy)
    sc = await screen(load_image(read_document(cu, doc), doc.mime), modality)
    prompt = (f"You are helping a patient in India understand a medical image ({modality.replace('_', ' ')}). "
              "Describe what is visible in plain language, mention any areas that look unusual, and say clearly "
              "that only a radiologist or specialist can give a diagnosis. Do not state a definitive diagnosis, "
              "do not recommend medicines. Keep it under 150 words. "
              f"Answer in {lang}." + prompt_block(sc) + (f" The patient asks: {body.question}" if body.question else ""))
    try:
        d = await ollama().chat(s.vlm_model, [{"role": "user", "content": prompt}], images=[b64],
                                temperature=0.2, num_ctx=4096, num_predict=400)
    except ProviderUnavailable as e:
        raise HTTPException(503, e.hint) from e
    warn = ("MedGemma-4B has limited training on this image type; treat this description as very uncertain."
            if modality in LOW_CONFIDENCE_MODALITIES else None)
    return {"modality": modality, "supported": True, "description": d.text.strip(), "low_confidence_warning": warn,
            "screening": for_person(sc, body.lang),
            "model": s.vlm_model, "advice": "Please get this image reviewed by a radiologist or specialist.",
            "disclaimer": disclaimer(body.lang)}


# ---------------------------------------------------------------- report library, review, delete
class ReviewedValue(BaseModel):
    test_name: str = Field(min_length=1, max_length=120)
    loinc_code: str | None = Field(None, max_length=12)
    value: float | None = None
    value_text: str | None = Field(None, max_length=60)
    unit: str | None = Field(None, max_length=30)
    ref_low: float | None = None
    ref_high: float | None = None


class ConfirmIn(BaseModel):
    results: list[ReviewedValue] = Field(max_length=200)
    report_date: str | None = None  # YYYY-MM-DD printed on the report (defaults to the upload day)
    title: str | None = Field(None, max_length=120)


def _flag(v: ReviewedValue) -> str | None:
    if v.value is None or (v.ref_low is None and v.ref_high is None):
        return None
    return "L" if v.ref_low is not None and v.value < v.ref_low else "H" if v.ref_high is not None and v.value > v.ref_high else "N"


async def _own_doc(db: AsyncSession, cu: CurrentUser, doc_id: str) -> Document:
    doc = await db.get(Document, doc_id)
    if doc is None or doc.user_id != cu.id:
        raise HTTPException(404, "Not found")
    return doc


@router.get("/reports/library")
async def library(cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Every uploaded document with what was read from it: reports (values, abnormal count, reviewed or not),
    prescriptions and scans. Newest first."""
    docs = list(await db.scalars(select(Document).where(Document.user_id == cu.id).order_by(Document.uploaded_at.desc())))
    labs = list(await db.scalars(select(LabResult).where(LabResult.user_id == cu.id)))
    rx = {p.document_id for p in await db.scalars(select(Prescription).where(Prescription.user_id == cu.id)) if p.document_id}
    by_doc: dict[str, list[LabResult]] = {}
    for lr in labs:
        if lr.document_id:
            by_doc.setdefault(lr.document_id, []).append(lr)
    out = []
    for d in docs:
        rows = by_doc.get(d.id, [])
        pending = d.extracted or []
        out.append({
            "id": d.id, "kind": d.kind, "mime": d.mime, "uploaded_at": d.uploaded_at, "report_date": d.report_date,
            "title": d.title, "reviewed": d.reviewed_at is not None or bool(rows),
            "values": len(rows) or len(pending),
            "abnormal": sum(r.flag in ("H", "L") for r in rows) if rows else sum((p.get("flag") in ("H", "L")) for p in pending),
            "abnormal_names": [r.test_name for r in rows if r.flag in ("H", "L")][:4],
            "in_prescriptions": d.id in rx,
        })
    return out


@router.get("/reports/{doc_id}")
async def get_report(doc_id: str, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """A report for review: the saved values if checked already, else the values read from it (not yet saved)."""
    doc = await _own_doc(db, cu, doc_id)
    rows = list(await db.scalars(select(LabResult).where(LabResult.user_id == cu.id, LabResult.document_id == doc_id)))
    values = ([{"test_name": r.test_name, "loinc_code": r.loinc_code, "value": r.value, "value_text": r.value_text, "unit": r.unit,
                "ref_low": r.ref_low, "ref_high": r.ref_high, "flag": r.flag} for r in rows] if rows else (doc.extracted or []))
    return {"id": doc.id, "kind": doc.kind, "mime": doc.mime, "uploaded_at": doc.uploaded_at, "title": doc.title,
            "report_date": doc.report_date, "reviewed": doc.reviewed_at is not None or bool(rows), "values": values}


@router.post("/reports/{doc_id}/confirm")
async def confirm_report(doc_id: str, body: ConfirmIn, cu: CurrentUser = Depends(current_user),
                         db: AsyncSession = Depends(get_db)):
    """Save the values the person checked (and corrected) against the document. Replaces earlier values of this
    report, so it can be re-reviewed any time."""
    from datetime import date as _date
    from datetime import datetime as _dt

    purposes = await active_purposes(db, cu.account_id)
    if "history_storage" not in purposes:
        raise HTTPException(403, "Saving reports needs the 'save my health history' choice in Privacy")
    doc = await _own_doc(db, cu, doc_id)
    day = None
    if body.report_date:
        try:
            day = _date.fromisoformat(body.report_date)
        except ValueError:
            raise HTTPException(422, "report_date must be YYYY-MM-DD") from None
    from sqlalchemy import delete
    await db.execute(delete(LabResult).where(LabResult.user_id == cu.id, LabResult.document_id == doc_id))
    observed = _dt.combine(day, _dt.min.time()) if day else doc.uploaded_at
    for v in body.results:
        db.add(LabResult(user_id=cu.id, document_id=doc.id, test_name=v.test_name.strip(), loinc_code=v.loinc_code, value=v.value,
                         value_text=v.value_text or (f"{v.value:g}" if v.value is not None else None), unit=v.unit,
                         ref_low=v.ref_low, ref_high=v.ref_high, flag=_flag(v), observed_at=observed))
    doc.report_date, doc.title, doc.reviewed_at = day, (body.title or doc.title), _now()
    doc.extracted = [v.model_dump() | {"flag": _flag(v)} for v in body.results]
    await db.commit()
    return {"saved": len(body.results), "abnormal": sum(_flag(v) in ("H", "L") for v in body.results)}


@router.delete("/reports/{doc_id}", status_code=204)
async def delete_report(doc_id: str, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Delete a report or scan: the file and every value read from it. Prescriptions are deleted from Medicines,
    where their medicines and reminders are handled."""
    from sqlalchemy import delete

    doc = await _own_doc(db, cu, doc_id)
    if (await db.scalars(select(Prescription.id).where(Prescription.user_id == cu.id, Prescription.document_id == doc_id))).first():
        raise HTTPException(409, "This is a prescription: delete it from Medicines")
    await db.execute(delete(LabResult).where(LabResult.user_id == cu.id, LabResult.document_id == doc_id))
    (get_settings().storage_dir / doc.object_key).unlink(missing_ok=True)
    await db.delete(doc)
    await db.commit()
