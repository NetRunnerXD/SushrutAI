"""Encrypted document storage (uploads are PHI). Files are AES-GCM encrypted with the user's DEK."""
from __future__ import annotations

import asyncio
import hashlib
import io
import logging
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db
from app.models import Document
from app.security.auth import CurrentUser, current_user

router = APIRouter(prefix="/documents", tags=["documents"])
log = logging.getLogger(__name__)


def _check(data: bytes, mime: str) -> dict | None:
    """Blur / darkness / size and health-relevance advice (app/ocr/check.py). Never blocks an upload."""
    try:
        from app.ocr.check import check_image
        from app.ocr.pipeline import load_image

        c = check_image(load_image(data, mime), pdf=mime == "application/pdf")
        rel, kind = c["relevance"] or {}, c["kind"] or {}
        return {"issue": c["issue"], "health": rel.get("health"), "sharpness": c["quality"]["sharpness"],
                "brightness": c["quality"]["brightness"], "kind": kind.get("kind"), "scan_type": kind.get("scan_type"),
                "lab_share": kind.get("lab_share"), "ms": c["ms"]}
    except Exception as e:  # noqa: BLE001 - advice only
        log.warning("image check failed: %s", e)
        return None

ALLOWED = {"image/jpeg", "image/png", "image/webp", "application/pdf"}
MAX_BYTES = 15 * 1024 * 1024


def _sniff(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:5] == b"%PDF-":
        return "application/pdf"
    return None


def _strip_exif(data: bytes, mime: str) -> bytes:
    """Re-encode images to drop EXIF (GPS location, device ids)."""
    if not mime.startswith("image/"):
        return data
    img = Image.open(io.BytesIO(data))
    img = img.convert("RGB") if img.mode not in ("RGB", "L") else img
    out = io.BytesIO()
    img.save(out, format="JPEG" if mime == "image/jpeg" else "PNG", quality=92)
    return out.getvalue()


def read_document(cu: CurrentUser, doc: Document) -> bytes:
    path = get_settings().storage_dir / doc.object_key
    return cu.cipher.dec_bytes(path.read_bytes())


@router.post("", status_code=201)
async def upload(file: UploadFile = File(...),
                 kind: Literal["prescription", "lab_report", "xray", "ct", "mri", "skin", "other"] = Form(...),
                 cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "File too large (max 15 MB)")
    mime = _sniff(data)
    if mime not in ALLOWED:
        raise HTTPException(415, "Only JPEG, PNG, WEBP or PDF files are accepted")
    data = _strip_exif(data, mime)
    if mime == "image/webp":
        mime = "image/png"
    doc = Document(user_id=cu.id, kind=kind, object_key="", sha256=hashlib.sha256(data).hexdigest(), mime=mime)
    doc.object_key = f"{cu.id}/{doc.id}.bin"
    path = get_settings().storage_dir / doc.object_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(cu.cipher.enc_bytes(data))
    db.add(doc)
    await db.commit()
    check = await asyncio.to_thread(_check, data, mime)
    return {"id": doc.id, "kind": kind, "mime": mime, "bytes": len(data), "check": check}


@router.delete("/{doc_id}", status_code=204)
async def remove(doc_id: str, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Delete an upload the person chose to retake. Refused once it has been read into a prescription or labs."""
    from sqlalchemy import select

    from app.models import LabResult, Prescription

    doc = await db.get(Document, doc_id)
    if doc is None or doc.user_id != cu.id:
        raise HTTPException(404, "Not found")
    for M in (Prescription, LabResult):
        if (await db.scalars(select(M.id).where(M.user_id == cu.id, M.document_id == doc_id))).first():
            raise HTTPException(409, "This document is already part of your records")
    (get_settings().storage_dir / doc.object_key).unlink(missing_ok=True)
    await db.delete(doc)
    await db.commit()


@router.get("/{doc_id}")
async def meta(doc_id: str, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    doc = await db.get(Document, doc_id)
    if doc is None or doc.user_id != cu.id:
        raise HTTPException(404, "Not found")
    return {"id": doc.id, "kind": doc.kind, "mime": doc.mime, "uploaded_at": doc.uploaded_at}


@router.get("/{doc_id}/content")
async def content(doc_id: str, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    from fastapi import Response

    doc = await db.get(Document, doc_id)
    if doc is None or doc.user_id != cu.id:
        raise HTTPException(404, "Not found")
    return Response(read_document(cu, doc), media_type=doc.mime, headers={"Cache-Control": "private, no-store"})
