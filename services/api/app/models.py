"""ORM models. Columns ending in `_enc` hold AES-256-GCM ciphertext (per-user DEK).

Portable across SQLite (dev, no Docker) and PostgreSQL (prod). On Postgres, enable RLS
with infra/sql/rls.sql.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    event,
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


@event.listens_for(Base, "init", propagate=True)
def _id_on_create(target, args, kwargs) -> None:
    """Give a new row its id as soon as it is created. `default=_uuid` alone only fills it at INSERT, and code reads
    `.id` before that: upload file names (every upload was stored as "<user>/None.bin", each overwriting the last)
    and the prescription job's result id (the page then showed "Not found" until refreshed)."""
    table = getattr(type(target), "__table__", None)
    col = table.c.get("id") if table is not None else None
    if col is not None and col.primary_key and col.default is not None and kwargs.get("id") is None:
        kwargs["id"] = _uuid()  # the init event may alter the constructor kwargs in place


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)  # naive UTC everywhere (SQLite + PG portable)


# ---------------------------------------------------------------- identity & consent
class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    phone_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    email_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    phone_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    email_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    preferred_lang: Mapped[str] = mapped_column(String(8), default="en")
    is_guest: Mapped[bool] = mapped_column(Boolean, default=False)
    dek_wrapped: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)


class HouseholdMember(Base):
    """A family member whose health the account holder manages. The member is a separate User (own DEK,
    own rows, never logs in); user_id is the account holder (guardian)."""
    __tablename__ = "household_members"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    member_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    name_enc: Mapped[bytes] = mapped_column(LargeBinary)  # encrypted with the account holder's DEK
    relation: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class OtpChallenge(Base):
    __tablename__ = "otp_challenges"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    identifier_hash: Mapped[str] = mapped_column(String(64), index=True)
    channel: Mapped[str] = mapped_column(String(8))  # email | phone
    code_hash: Mapped[str] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)


class Consent(Base):
    __tablename__ = "consents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(40))
    notice_version: Mapped[str] = mapped_column(String(20))
    granted_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime)


class ConsentEvent(Base):
    """Append-only consent ledger (DPDP evidence)."""

    __tablename__ = "consent_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), index=True)  # kept after erasure as hash-free id
    purpose: Mapped[str] = mapped_column(String(40))
    event: Mapped[str] = mapped_column(String(16))  # granted | withdrawn
    notice_version: Mapped[str] = mapped_column(String(20))
    at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class UserSettings(Base):
    __tablename__ = "user_settings"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    offline_mode: Mapped[bool] = mapped_column(Boolean, default=False)  # force all AI to run locally (tier L)
    reminder_times: Mapped[dict] = mapped_column(JSON, default=lambda: {"morning": "08:00", "afternoon": "14:00",
                                                                        "night": "21:00"})
    large_text: Mapped[bool] = mapped_column(Boolean, default=False)
    voice_autoplay: Mapped[bool] = mapped_column(Boolean, default=True)
    model_pref: Mapped[str] = mapped_column(String(12), default="auto")  # auto | balanced | fast | instant | cloud


class FollowUp(Base):
    """'Check in with me': a question to come back to on a given day (from a chat's warning signs or symptoms).
    Shown on Home when due; tapping it asks the assistant."""

    __tablename__ = "follow_ups"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    text_enc: Mapped[bytes] = mapped_column(LargeBinary)
    due_day: Mapped[date] = mapped_column(Date, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class VisitQuestion(Base):
    """A question the person wants to ask at their next doctor visit (saved from chat or typed)."""

    __tablename__ = "visit_questions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    text_enc: Mapped[bytes] = mapped_column(LargeBinary)
    source: Mapped[str] = mapped_column(String(12), default="manual")  # chat | manual
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class CheckIn(Base):
    """One-tap daily check-in from Home: how the person feels today (one row per day; re-tapping updates it)."""

    __tablename__ = "checkins"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date)
    mood: Mapped[str] = mapped_column(String(8))  # good | okay | unwell
    note_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_checkin_day"),)


class CareRecord(Base):
    """A vaccine dose or pregnancy check-up marked done (the schedule itself is in app/care/schedules.py)."""

    __tablename__ = "care_records"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    item_code: Mapped[str] = mapped_column(String(32))
    done_on: Mapped[date] = mapped_column(Date)
    note_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    __table_args__ = (UniqueConstraint("user_id", "item_code", name="uq_care_item"),)


class Pregnancy(Base):
    """Current pregnancy, for the check-up (ANC) schedule. The LMP date is health data: encrypted."""

    __tablename__ = "pregnancies"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    lmp_enc: Mapped[bytes] = mapped_column(LargeBinary)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class DoseLog(Base):
    """Taken / skipped record for one scheduled dose (adherence tracking)."""

    __tablename__ = "dose_logs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    medication_id: Mapped[str] = mapped_column(String(36))
    slot: Mapped[str] = mapped_column(String(12))  # morning | afternoon | night | as_needed
    day: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(8))  # taken | skipped
    at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    __table_args__ = (UniqueConstraint("user_id", "medication_id", "slot", "day", name="uq_dose_slot_day"),)


class LabResult(Base):
    __tablename__ = "lab_results"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[str | None] = mapped_column(String(36))
    test_name: Mapped[str] = mapped_column(String(120))
    loinc_code: Mapped[str | None] = mapped_column(String(12))
    value: Mapped[float | None] = mapped_column(Float)
    value_text: Mapped[str | None] = mapped_column(String(60))
    unit: Mapped[str | None] = mapped_column(String(30))
    ref_low: Mapped[float | None] = mapped_column(Float)
    ref_high: Mapped[float | None] = mapped_column(Float)
    flag: Mapped[str | None] = mapped_column(String(8))  # L | H | N
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# ---------------------------------------------------------------- profile
class PatientProfile(Base):
    __tablename__ = "patient_profiles"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    dob_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    sex: Mapped[str | None] = mapped_column(String(16))
    gender_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    blood_group: Mapped[str | None] = mapped_column(String(4))
    height_cm: Mapped[float | None] = mapped_column(Float)
    state_code: Mapped[str | None] = mapped_column(String(4))
    district: Mapped[str | None] = mapped_column(String(64))
    diet: Mapped[str | None] = mapped_column(String(16))
    smoking_status: Mapped[str | None] = mapped_column(String(16))
    pack_years: Mapped[float | None] = mapped_column(Float)
    tobacco_chewing: Mapped[bool | None] = mapped_column(Boolean)
    alcohol_units_wk: Mapped[float | None] = mapped_column(Float)
    activity_level: Mapped[str | None] = mapped_column(String(16))
    pregnancy_status: Mapped[str | None] = mapped_column(String(16))
    lactating: Mapped[bool | None] = mapped_column(Boolean)
    surgeries_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    family_history_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    emergency_contact_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class Allergy(Base):
    __tablename__ = "allergies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    substance_enc: Mapped[bytes] = mapped_column(LargeBinary)
    type: Mapped[str] = mapped_column(String(12))  # drug | food | other
    reaction_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    severity: Mapped[str | None] = mapped_column(String(12))
    generic_id: Mapped[str | None] = mapped_column(String(36))  # resolved drug, for cross-checks


class Condition(Base):
    __tablename__ = "conditions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    display_enc: Mapped[bytes] = mapped_column(LargeBinary)
    snomed_code: Mapped[str | None] = mapped_column(String(20))
    icd10: Mapped[str | None] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(12), default="active")
    onset_date: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(24), default="user")


class Medication(Base):
    __tablename__ = "medications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name_enc: Mapped[bytes] = mapped_column(LargeBinary)
    product_id: Mapped[str | None] = mapped_column(String(36))
    generic_id: Mapped[str | None] = mapped_column(String(36))
    dose: Mapped[str | None] = mapped_column(String(40))
    frequency: Mapped[dict | None] = mapped_column(JSON)
    timing: Mapped[str | None] = mapped_column(String(20))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    prescription_item_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(12), default="active")


class Vital(Base):
    __tablename__ = "vitals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(16))
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(12))
    measured_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    source: Mapped[str] = mapped_column(String(16), default="user")
    __table_args__ = (Index("ix_vitals_user_type_time", "user_id", "type", "measured_at"),)


# ---------------------------------------------------------------- documents & prescriptions
class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    object_key: Mapped[str] = mapped_column(String(200))
    sha256: Mapped[str] = mapped_column(String(64))
    mime: Mapped[str] = mapped_column(String(64))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    # report review: values read by the model wait here until the person checks them against the document
    title: Mapped[str | None] = mapped_column(String(120), nullable=True)
    report_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    extracted: Mapped[list | None] = mapped_column(JSON, nullable=True)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(12), default="queued")  # queued|running|done|error
    progress: Mapped[list] = mapped_column(JSON, default=list)
    result_ref: Mapped[str | None] = mapped_column(String(36))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Prescription(Base):
    __tablename__ = "prescriptions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[str | None] = mapped_column(String(36))
    parsed_enc: Mapped[bytes] = mapped_column(LargeBinary)  # full structured JSON (doctor, diagnosis, items…)
    raw_ocr_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    pipeline_version: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(12), default="draft")  # draft | confirmed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)


# ---------------------------------------------------------------- assistant
class ChatSession(Base):
    __tablename__ = "chat_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    lang: Mapped[str] = mapped_column(String(8), default="en")
    channel: Mapped[str] = mapped_column(String(8), default="text")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    title_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)  # user-chosen name (encrypted)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(12))
    content_enc: Mapped[bytes] = mapped_column(LargeBinary)
    lang: Mapped[str] = mapped_column(String(8))
    structured: Mapped[dict | None] = mapped_column(JSON)
    citations: Mapped[list | None] = mapped_column(JSON)
    safety: Mapped[dict | None] = mapped_column(JSON)
    provider: Mapped[str | None] = mapped_column(String(24))
    model: Mapped[str | None] = mapped_column(String(64))
    privacy_tier: Mapped[str | None] = mapped_column(String(4))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class TriageSession(Base):
    __tablename__ = "triage_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    state: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(12), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# ---------------------------------------------------------------- drug master (public data, not PHI)
class Generic(Base):
    __tablename__ = "drug_generics"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    inn_name: Mapped[str] = mapped_column(String(120), unique=True)  # lower-case INN, e.g. "paracetamol"
    usan_name: Mapped[str | None] = mapped_column(String(120))  # e.g. "acetaminophen" (for openFDA)
    atc_code: Mapped[str | None] = mapped_column(String(10))
    nlem: Mapped[bool] = mapped_column(Boolean, default=False)
    nti: Mapped[bool] = mapped_column(Boolean, default=False)


class Product(Base):
    """A marketed formulation: Jan Aushadhi generic, or a branded product."""

    __tablename__ = "drug_products"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(300), index=True)  # brand name, or JA generic name
    name_norm: Mapped[str] = mapped_column(String(300), index=True)
    manufacturer: Mapped[str | None] = mapped_column(String(160))
    composition_key: Mapped[str] = mapped_column(String(400), index=True)  # canonical, e.g. "paracetamol:650mg"
    composition: Mapped[list] = mapped_column(JSON)  # [{"generic":"paracetamol","strength":650,"unit":"mg"}]
    form: Mapped[str | None] = mapped_column(String(24))
    release_type: Mapped[str] = mapped_column(String(8), default="IR")  # IR | SR | ER | MR | DR | GR
    pack_size: Mapped[float | None] = mapped_column(Float)
    pack_unit: Mapped[str | None] = mapped_column(String(16))
    category: Mapped[str | None] = mapped_column(String(120))
    is_janaushadhi: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(40))
    source_ref: Mapped[str | None] = mapped_column(String(80))
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (UniqueConstraint("source", "source_ref", name="uq_product_source_ref"),)


class Price(Base):
    __tablename__ = "drug_prices"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    product_id: Mapped[str] = mapped_column(ForeignKey("drug_products.id", ondelete="CASCADE"), index=True)
    mrp_inr: Mapped[float | None] = mapped_column(Float)
    per_unit_inr: Mapped[float | None] = mapped_column(Float)
    ceiling_price_inr: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(40))
    source_url: Mapped[str | None] = mapped_column(String(300))
    observed_on: Mapped[date] = mapped_column(Date)


class Interaction(Base):
    __tablename__ = "drug_interactions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    generic_a: Mapped[str] = mapped_column(String(120), index=True)
    generic_b: Mapped[str] = mapped_column(String(120), index=True)
    severity: Mapped[str] = mapped_column(String(16))  # Major | Moderate | Minor | Unknown
    source: Mapped[str] = mapped_column(String(40))


class BannedFdc(Base):
    __tablename__ = "drug_banned_fdcs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    composition_key: Mapped[str] = mapped_column(String(400), index=True)
    description: Mapped[str] = mapped_column(Text)
    notification_ref: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(24), default="in_force")  # in_force|stayed|revoked_conditional|quashed_2016
    status_note: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(String(300))


class MonographCache(Base):
    __tablename__ = "drug_monograph_cache"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    generic: Mapped[str] = mapped_column(String(120), unique=True)
    payload: Mapped[dict] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(40))
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# ---------------------------------------------------------------- knowledge base (RAG)
class KbChunk(Base):
    __tablename__ = "kb_chunks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    collection: Mapped[str] = mapped_column(String(32), index=True)
    source_id: Mapped[str] = mapped_column(String(200), index=True)
    source_url: Mapped[str | None] = mapped_column(String(400))
    lang: Mapped[str] = mapped_column(String(8), default="en")
    title: Mapped[str] = mapped_column(String(300))
    breadcrumb: Mapped[str | None] = mapped_column(String(400))
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary)  # float32 array (pgvector in prod)
    version: Mapped[str | None] = mapped_column(String(40))


# ---------------------------------------------------------------- governance
class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    actor: Mapped[str] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(40))
    resource: Mapped[str] = mapped_column(String(40))
    resource_id: Mapped[str | None] = mapped_column(String(36))
    detail: Mapped[dict | None] = mapped_column(JSON)


class DataRequest(Base):
    __tablename__ = "data_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    type: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(12), default="open")
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
