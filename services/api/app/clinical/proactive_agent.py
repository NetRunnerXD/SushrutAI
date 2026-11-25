"""Proactive Follow-Up Agent for Patient & Family Health Continuity.

Generates consent-aware, patient-specific follow-up workflows from:
  - Consultation history & clinician/triage recommendations
  - Pending / unconfirmed lab reports
  - Unanswered clinical follow-up questions
  - Chronic condition monitoring intervals (e.g., Diabetes glucose, Hypertension BP)

SAFETY: Never autonomously prescribes or changes medication.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import scope_to
from app.models import (
    AuditEvent,
    ClinicalRecommendation,
    Condition,
    Document,
    Encounter,
    FollowUpQuestion,
    Vital,
)
from app.security.auth import CurrentUser
from app.security.consent import active_purposes


def _msg(lang: str, en: str, bn: str, hi: str) -> str:
    return bn if lang == "bn" else hi if lang == "hi" else en


async def build_proactive_followup_plan(
    db: AsyncSession,
    cu: CurrentUser,
    *,
    lang: str = "en",
) -> dict[str, Any]:
    """Build a prioritized proactive follow-up plan for `cu.id` respecting consents."""
    consents = await active_purposes(db, cu.account_id)
    if "core_service" not in consents:
        return {"consent_required": True, "items": []}

    await scope_to(db, cu.id, cu.account_id)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    items: list[dict[str, Any]] = []

    # 1. Pending Follow-Up Questions from ongoing consultations
    pending_fqs = list(await db.scalars(
        select(FollowUpQuestion)
        .where(FollowUpQuestion.user_id == cu.id, FollowUpQuestion.status == "pending")
        .order_by(FollowUpQuestion.priority.asc())
    ))
    for fq in pending_fqs[:3]:
        q_text = cu.cipher.dec(fq.question_enc)
        items.append({
            "id": f"fq:{fq.id}",
            "kind": "pending_question",
            "priority": "high" if fq.priority <= 1 else "medium",
            "title": _msg(lang, "Continue consultation check-in", "পরামর্শের পরবর্তী প্রশ্ন", "परामर्श का अगला प्रश्न"),
            "message": q_text,
            "requires_clinician": False,
        })

    # 2. Pending / unconfirmed Lab Reports
    docs = list(await db.scalars(
        select(Document).where(Document.user_id == cu.id, Document.kind == "lab_report")
    ))
    for doc in docs[:3]:
        if not getattr(doc, "confirmed_at", None):
            items.append({
                "id": f"doc:{doc.id}",
                "kind": "pending_report",
                "priority": "medium",
                "title": _msg(lang, "Pending lab report confirmation", "ল্যাব রিপোর্ট নিশ্চিতকরণ বাকি", "लैब रिपोर्ट की पुष्टि बाकी"),
                "message": _msg(
                    lang,
                    "Please review and confirm your uploaded lab report values.",
                    "আপলোড করা ল্যাব রিপোর্টের মানগুলো যাচাই করে নিশ্চিত করুন।",
                    "अपलोड की गई लैब रिपोर्ट के मान जांच कर पुष्टि करें।",
                ),
                "document_id": doc.id,
            })

    # 3. Appointment / Doctor Follow-Up from recent recommendations
    recs = list(await db.scalars(
        select(ClinicalRecommendation)
        .where(ClinicalRecommendation.user_id == cu.id)
        .order_by(ClinicalRecommendation.created_at.desc())
    ))
    if recs:
        latest_rec = recs[0]
        if latest_rec.urgency in ("see_doctor_24h", "emergency"):
            items.append({
                "id": f"appt:{latest_rec.id}",
                "kind": "appointment_followup",
                "priority": "high",
                "title": _msg(lang, "Doctor consultation follow-up", "চিকিৎসকের পরামর্শ ফলো-আপ", "डॉक्टर परामर्श फॉलो-अप"),
                "message": _msg(
                    lang,
                    f"Follow up with {latest_rec.specialist or 'a doctor'} ({latest_rec.urgency.replace('_', ' ')}).",
                    f"অনুগ্রহ করে {latest_rec.specialist or 'চিকিৎসক'}-এর সাথে দেখা করুন।",
                    f"कृपया {latest_rec.specialist or 'डॉक्टर'} से परामर्श लें।",
                ),
                "specialist": latest_rec.specialist,
            })

    # 4. Chronic Disease Vital Monitoring Reminders (Diabetes / Hypertension > 7 days)
    conds = [cu.cipher.dec(c.display_enc).lower() for c in await db.scalars(
        select(Condition).where(Condition.user_id == cu.id, Condition.status == "active")
    )]
    stale_cutoff = now - timedelta(days=7)
    if any("hypertension" in c or "bp" in c for c in conds):
        latest_bp = (await db.scalars(
            select(Vital).where(Vital.user_id == cu.id, Vital.type == "bp_sys").order_by(Vital.measured_at.desc())
        )).first()
        if latest_bp is None or (latest_bp.measured_at and latest_bp.measured_at < stale_cutoff):
            items.append({
                "id": "monitor:bp",
                "kind": "vital_reminder",
                "priority": "medium",
                "title": _msg(lang, "Blood pressure check reminder", "রক্তচাপ মাপার রিমাইন্ডার", "ब्लड प्रेशर जांच रिमाइंडर"),
                "message": _msg(
                    lang,
                    "It has been over 7 days since your last recorded BP reading. Consider checking it.",
                    "শেষবার BP মাপার ৭ দিনের বেশি হয়ে গেছে। অনুগ্রহ করে রক্তচাপ মেপে নিন।",
                    "पिछली बार बीपी मापे 7 दिन से अधिक हो गए हैं। कृपया बीपी जांचें।",
                ),
            })
    if any("diabetes" in c or "sugar" in c for c in conds):
        latest_glu = (await db.scalars(
            select(Vital).where(Vital.user_id == cu.id, Vital.type.like("glucose_%")).order_by(Vital.measured_at.desc())
        )).first()
        if latest_glu is None or (latest_glu.measured_at and latest_glu.measured_at < stale_cutoff):
            items.append({
                "id": "monitor:glucose",
                "kind": "vital_reminder",
                "priority": "medium",
                "title": _msg(lang, "Blood sugar check reminder", "সুগার মাপার রিমাইন্ডার", "ब्लड शुगर जांच रिमाइंडर"),
                "message": _msg(
                    lang,
                    "It has been over 7 days since your last blood sugar reading.",
                    "শেষবার ব্লাড সুগার মাপার ৭ দিনের বেশি হয়ে গেছে।",
                    "पिछली बार ब्लड शुगर मापे 7 दिन से अधिक हो गए हैं।",
                ),
            })

    return {
        "patient_id": cu.id,
        "consent_active": True,
        "reminders_consent": "reminders" in consents or "core_service" in consents,
        "items": items,
        "count": len(items),
    }


async def acknowledge_followup_item(
    db: AsyncSession,
    cu: CurrentUser,
    *,
    item_id: str,
    note: str = "",
) -> dict[str, Any]:
    """Acknowledge a proactive follow-up item with encrypted audit logging."""
    await scope_to(db, cu.id, cu.account_id)
    db.add(AuditEvent(
        user_id=cu.id,
        actor_id=cu.account_id,
        actor_role="patient",
        action="proactive_followup_acknowledged",
        entity_type="followup_item",
        entity_id=item_id[:36],
        detail_enc=cu.cipher.enc(note[:300]) if note else None,
    ))
    await db.commit()
    return {"acknowledged": True, "item_id": item_id}
