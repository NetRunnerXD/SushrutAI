"""Automatic Conversation Memory & Doctor-Friendly PDF Medical History Report.

Every consultation (text or live voice) automatically produces and encrypts a
structured, 18-field clinical summary persisted to `Encounter.summary_enc`,
strictly scoped to the target patient/family-member profile and gated by consent.

Also generates a real downloadable PDF medical history report via PyMuPDF (fitz).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pymupdf
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clinical.digital_twin import build_digital_twin
from app.clinical.pipeline_12layer import run_12layer_clinical_pipeline
from app.db import scope_to
from app.models import AuditEvent, Encounter, HouseholdMember
from app.security.auth import CurrentUser
from app.security.consent import active_purposes


async def save_conversation_summary(
    db: AsyncSession,
    cu: CurrentUser,
    *,
    conversation_id: str,
    user_text: str,
    channel: str = "text",
    lang: str = "en",
    pipeline_result: dict[str, Any] | None = None,
    doctor_consultation_status: str = "pending_clinician_review",
) -> dict[str, Any] | None:
    """Extract & persist a structured 18-field conversation summary on the target patient's Encounter."""
    consents = await active_purposes(db, cu.account_id)
    if "core_service" not in consents:
        return None

    await scope_to(db, cu.id, cu.account_id)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    pipe = pipeline_result or run_12layer_clinical_pipeline(user_text, lang=lang, patient_id=cu.id)
    twin = await build_digital_twin(db, cu)

    ss = pipe.get("structured_summary") or {}
    routing = pipe.get("routing") or {}
    invs = pipe.get("investigations") or []
    diffs = pipe.get("differentials") or []
    fqs = pipe.get("followup_questions") or []

    summary_obj: dict[str, Any] = {
        "patient_id": cu.id,
        "conversation_id": conversation_id,
        "date_and_time": now.isoformat() + "Z",
        "channel": channel,
        "language": lang,
        "main_complaint": ss.get("chief_complaint") or user_text[:160],
        "symptoms": pipe.get("symptoms") or [],
        "duration": ss.get("duration"),
        "severity": ss.get("severity"),
        "medical_history": [c["name"] for c in twin["current_state"]["conditions"]],
        "medication_history": [m["name"] for m in twin["current_state"]["medications"]],
        "allergies": [a["substance"] for a in twin["allergies"]],
        "family_history": ss.get("family_history") or [],
        "investigation_history": [
            f"{lr['test_name']}: {lr['value']} {lr['unit'] or ''}".strip()
            for lr in twin["lab_reports"][:10]
        ],
        "clinical_observations": [
            f"{k}: {v['value']} {v['unit']}".strip()
            for k, v in twin["current_state"]["vitals"].items()
        ],
        "possible_health_concerns": [
            {"condition": d["condition"], "likelihood": d.get("likelihood_category", "Possible"), "confirmed": False}
            for d in diffs
        ],
        "suggested_specialist": routing.get("primary_department"),
        "suggested_investigations": [i.get("investigation_name") for i in invs],
        "doctor_consultation_status": doctor_consultation_status,
        "follow_up_recommendations": fqs,
    }

    enc = (await db.scalars(
        select(Encounter).where(Encounter.user_id == cu.id, Encounter.chat_session_id == conversation_id)
    )).first()
    if enc is not None and enc.summary_enc:
        try:
            prev = json.loads(cu.cipher.dec(enc.summary_enc) or "{}")
        except Exception:  # noqa: BLE001
            prev = {}
        if prev.get("main_complaint") and prev["main_complaint"] != summary_obj["main_complaint"]:
            summary_obj["main_complaint"] = prev["main_complaint"]
        prev_syms = list(prev.get("symptoms") or [])
        for s_item in summary_obj["symptoms"]:
            if s_item not in prev_syms:
                prev_syms.append(s_item)
        summary_obj["symptoms"] = prev_syms
        if prev.get("compact_summary"):
            summary_obj["compact_summary"] = prev["compact_summary"]
    if enc is None:
        enc = Encounter(
            user_id=cu.id,
            chat_session_id=conversation_id,
            channel=channel,
            language=lang,
            chief_complaint_enc=cu.cipher.enc((summary_obj["main_complaint"] or "")[:240]),
            summary_enc=cu.cipher.enc(json.dumps(summary_obj, ensure_ascii=False)),
            urgency=routing.get("urgency_api"),
            specialist=routing.get("primary_department"),
        )
        db.add(enc)
    else:
        enc.summary_enc = cu.cipher.enc(json.dumps(summary_obj, ensure_ascii=False))
        enc.urgency = routing.get("urgency_api") or enc.urgency
        enc.specialist = routing.get("primary_department") or enc.specialist

    await db.flush()
    db.add(AuditEvent(
        user_id=cu.id,
        actor_id=cu.account_id,
        actor_role="system",
        action="conversation_summary_saved",
        entity_type="encounter",
        entity_id=enc.id,
        detail_enc=None,
    ))
    await db.commit()
    summary_obj["encounter_id"] = enc.id
    return summary_obj


async def list_conversation_summaries(
    db: AsyncSession,
    cu: CurrentUser,
    *,
    query: str = "",
) -> list[dict[str, Any]]:
    """List & search encrypted conversation summaries for `cu.id`."""
    await scope_to(db, cu.id, cu.account_id)
    rows = list(await db.scalars(
        select(Encounter).where(Encounter.user_id == cu.id).order_by(Encounter.started_at.desc())
    ))
    out: list[dict[str, Any]] = []
    q_low = (query or "").strip().lower()
    for enc in rows:
        if not enc.summary_enc:
            continue
        try:
            data = json.loads(cu.cipher.dec(enc.summary_enc))
        except Exception:  # noqa: BLE001
            continue
        data["encounter_id"] = enc.id
        if q_low and q_low not in json.dumps(data, ensure_ascii=False).lower():
            continue
        out.append(data)
    return out


async def generate_medical_history_pdf(
    db: AsyncSession,
    cu: CurrentUser,
    *,
    member_name: str | None = None,
) -> bytes:
    """Generate a clean, doctor-friendly downloadable PDF medical history report using PyMuPDF."""
    await scope_to(db, cu.id, cu.account_id)
    twin = await build_digital_twin(db, cu, member_name=member_name)
    summaries = await list_conversation_summaries(db, cu)

    if not member_name and cu.is_member:
        link = (await db.scalars(
            select(HouseholdMember).where(HouseholdMember.user_id == cu.account_id, HouseholdMember.member_id == cu.id)
        )).first()
        if link:
            member_name = cu.account_cipher.dec(link.name_enc)

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)  # A4
    y = 48.0

    def write_line(text: str, *, size: float = 10.5, bold: bool = False, gap: float = 15.0) -> None:
        nonlocal page, y
        if y > 790:
            page = doc.new_page(width=595, height=842)
            y = 48.0
        font = "helv" if not bold else "hebo"
        # Wrap long lines safely at ~88 chars
        words = (text or "").split()
        lines: list[str] = []
        cur = ""
        for w in words:
            if len(cur) + len(w) + 1 > 88:
                lines.append(cur)
                cur = w
            else:
                cur = f"{cur} {w}".strip()
        if cur:
            lines.append(cur)
        if not lines:
            lines = [""]
        for ln in lines:
            if y > 790:
                page = doc.new_page(width=595, height=842)
                y = 48.0
            page.insert_text((42, y), ln, fontsize=size, fontname=font)
            y += gap

    write_line("SUSHRUTAI - DOCTOR-FRIENDLY PATIENT MEDICAL HISTORY REPORT", size=13.5, bold=True, gap=20)
    write_line(
        f"Patient: {member_name or 'Account Holder'} | ID: {cu.id} | Age: {twin['demographics'].get('age') or 'Not recorded'} | Sex: {twin['demographics'].get('sex') or 'Not recorded'}",
        size=10,
    )
    write_line(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} (Informational summary - confirm clinically)", size=9)
    y += 8

    write_line("1. ACTIVE CONDITIONS & ALLERGIES", size=11.5, bold=True, gap=16)
    conds = [c["name"] for c in twin["current_state"]["conditions"]]
    allergs = [a["substance"] for a in twin["allergies"]]
    write_line("Active Conditions: " + (", ".join(conds) if conds else "None recorded"))
    write_line("Known Allergies: " + (", ".join(allergs) if allergs else "None recorded"))
    y += 6

    write_line("2. CURRENT MEDICATIONS", size=11.5, bold=True, gap=16)
    meds = [f"{m['name']} ({m.get('dose') or 'dose not recorded'})" for m in twin["current_state"]["medications"]]
    write_line(", ".join(meds) if meds else "None recorded")
    y += 6

    write_line("3. VITAL SIGNS (CURRENT vs HISTORICAL)", size=11.5, bold=True, gap=16)
    if not twin["current_state"]["vitals"]:
        write_line("No vitals recorded.")
    for vt, cur in twin["current_state"]["vitals"].items():
        hist_n = len(twin["historical_state"]["vitals"].get(vt, []))
        write_line(f"- {vt}: {cur['value']} {cur['unit']} (recorded {cur['measured_at']}; {hist_n} prior readings)")
    y += 6

    write_line("4. LABORATORY REPORTS", size=11.5, bold=True, gap=16)
    if not twin["lab_reports"]:
        write_line("No laboratory results recorded.")
    for lr in twin["lab_reports"][:12]:
        write_line(f"- {lr['test_name']}: {lr['value']} {lr['unit'] or ''} [{lr['flag'] or 'N'}] ({lr['observed_at']})")
    y += 6

    write_line("5. RECENT CONSULTATION SUMMARIES", size=11.5, bold=True, gap=16)
    if not summaries:
        write_line("No consultation summaries recorded.")
    for s in summaries[:8]:
        write_line(f"- {s.get('date_and_time')}: {s.get('main_complaint')} | Symptoms: {', '.join(s.get('symptoms') or [])}")
        write_line(f"  Specialist: {s.get('suggested_specialist') or 'Pending'} | Tests: {', '.join(s.get('suggested_investigations') or []) or 'None'}")

    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes
