from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import local_today
from app.db import SessionLocal, get_db
from app.llm.providers import ProviderUnavailable
from app.medicine.parse import parse_composition
from app.models import Allergy, Document, Job, Medication, Prescription
from app.models import _now as utcnow
from app.ocr.pipeline import PIPELINE_VERSION, parse_prescription
from app.routers.documents import read_document
from app.security.auth import CurrentUser, current_user
from app.security.consent import active_purposes

router = APIRouter(tags=["prescriptions"])
log = logging.getLogger("sushrut.rx")
_TASKS: set[asyncio.Task] = set()


class ParseIn(BaseModel):
    document_id: str


async def _run_job(job_id: str, cu: CurrentUser, doc_id: str) -> None:
    async with SessionLocal() as db:
        job = await db.get(Job, job_id)
        doc = await db.get(Document, doc_id)

        async def progress(stage: str) -> None:
            job.progress = [*job.progress, stage]
            job.status = "running"
            await db.commit()

        try:
            allergies = [cu.cipher.dec(a.substance_enc) for a in
                         await db.scalars(select(Allergy).where(Allergy.user_id == cu.id))]
            current = []
            for m in await db.scalars(select(Medication).where(Medication.user_id == cu.id,
                                                               Medication.status == "active")):
                current += [i.generic for i in parse_composition(cu.cipher.dec(m.name_enc)).ingredients]
            from app.llm.providers import full_cloud_allowed
            from app.security.settings import cloud_allowed, get_settings_row

            pref = (await get_settings_row(db, cu.account_id)).model_pref or "balanced"
            full_cloud_allowed.set(await cloud_allowed(db, cu.account_id, "cloud_llm_full"))
            parsed = await parse_prescription(db, read_document(cu, doc), doc.mime, progress=progress,
                                              allergies=allergies, current_generics=current, model_pref=pref)
            raw_ocr = parsed.pop("raw_ocr", None)
            rx = Prescription(user_id=cu.id, document_id=doc.id, pipeline_version=PIPELINE_VERSION,
                              parsed_enc=cu.cipher.enc(json.dumps(parsed, ensure_ascii=False, default=str)),
                              raw_ocr_enc=cu.cipher.enc(raw_ocr) if raw_ocr else None)
            db.add(rx)
            job.status, job.result_ref = "done", rx.id
            await db.commit()
        except ProviderUnavailable as e:
            job.status, job.error = "error", e.hint  # plain-language next step (details are in the server log)
            log.warning("prescription job: %s", e)
            await db.commit()
        except Exception as e:  # noqa: BLE001 — surface any pipeline failure to the user
            log.exception("prescription job failed")
            job.status, job.error = "error", f"{type(e).__name__}: {e}"
            await db.commit()


@router.post("/prescriptions/parse", status_code=202)
async def parse(body: ParseIn, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    if "core_service" not in await active_purposes(db, cu.account_id):
        raise HTTPException(403, "Please accept the privacy notice first")
    doc = await db.get(Document, body.document_id)
    if doc is None or doc.user_id != cu.id:
        raise HTTPException(404, "Document not found")
    job = Job(user_id=cu.id, kind="prescription_parse", progress=["queued"])
    db.add(job)
    await db.commit()
    t = asyncio.create_task(_run_job(job.id, cu, doc.id))
    _TASKS.add(t)
    t.add_done_callback(_TASKS.discard)
    return {"job_id": job.id}


@router.get("/jobs/{job_id}")
async def job_status(job_id: str, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    job = await db.get(Job, job_id)
    if job is None or job.user_id != cu.id:
        raise HTTPException(404, "Not found")
    return {"id": job.id, "status": job.status, "progress": job.progress, "result_id": job.result_ref,
            "error": job.error}


def _rx_out(rx: Prescription, cu: CurrentUser) -> dict:
    return {"id": rx.id, "document_id": rx.document_id, "status": rx.status, "created_at": rx.created_at,
            "confirmed_at": rx.confirmed_at, "pipeline_version": rx.pipeline_version,
            **json.loads(cu.cipher.dec(rx.parsed_enc))}


@router.get("/prescriptions")
async def list_rx(cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    rows = await db.scalars(select(Prescription).where(Prescription.user_id == cu.id)
                            .order_by(Prescription.created_at.desc()))
    return [_rx_out(r, cu) for r in rows]


@router.get("/prescriptions/{rx_id}")
async def get_rx(rx_id: str, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    rx = await db.get(Prescription, rx_id)
    if rx is None or rx.user_id != cu.id:
        raise HTTPException(404, "Not found")
    return _rx_out(rx, cu)


class RxPatch(BaseModel):
    medications: list[dict] | None = None
    diagnosis: list[str] | None = None
    tests: list[str] | None = None
    advice: list[str] | None = None
    doctor: dict | None = None
    date: str | None = None


@router.patch("/prescriptions/{rx_id}")
async def patch_rx(rx_id: str, body: RxPatch, cu: CurrentUser = Depends(current_user),
                   db: AsyncSession = Depends(get_db)):
    """User corrections (stored; reused for model improvement only with consent)."""
    rx = await db.get(Prescription, rx_id)
    if rx is None or rx.user_id != cu.id:
        raise HTTPException(404, "Not found")
    parsed = json.loads(cu.cipher.dec(rx.parsed_enc))
    for k, v in body.model_dump(exclude_unset=True).items():
        if k == "medications":
            for item in v:
                item["user_corrected"] = True
                item["needs_review"] = False
        parsed[k] = v
    rx.parsed_enc = cu.cipher.enc(json.dumps(parsed, ensure_ascii=False, default=str))
    await db.commit()
    return _rx_out(rx, cu)


@router.post("/prescriptions/{rx_id}/confirm")
async def confirm_rx(rx_id: str, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """User has checked the reading → add medicines to their active medication list."""
    rx = await db.get(Prescription, rx_id)
    if rx is None or rx.user_id != cu.id:
        raise HTTPException(404, "Not found")
    if rx.status == "confirmed":
        return _rx_out(rx, cu)
    parsed = json.loads(cu.cipher.dec(rx.parsed_enc))
    today = local_today()
    for i, it in enumerate(parsed.get("medications", [])):
        name = " ".join(x for x in (it.get("name"), it.get("strength")) if x)
        end = today.fromordinal(today.toordinal() + it["duration_days"]) if it.get("duration_days") else None
        db.add(Medication(user_id=cu.id, name_enc=cu.cipher.enc(name), product_id=it.get("product_id"),
                          dose=it.get("strength") or None, frequency=it.get("frequency"), timing=it.get("timing"),
                          start_date=today, end_date=end, prescription_item_id=f"{rx.id}:{i}"))
    rx.status, rx.confirmed_at = "confirmed", utcnow()
    from app.memory.trajectory import start_trajectory
    start_trajectory(db, cu, [it.get("name") or "" for it in parsed.get("medications", [])])  # day 1 / 2 check-ins
    await db.commit()
    return _rx_out(rx, cu)
