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
    tts_voice_gender: Mapped[str] = mapped_column(String(10), default="female")  # female | male
    tts_engine: Mapped[str] = mapped_column(String(16), default="auto")  # auto | kokoro | parler | mms
    translation_model: Mapped[str] = mapped_column(String(16), default="auto")  # auto | indictrans2 | llm | gemini
    sos_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    neural_correlation_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    speech_mode: Mapped[str] = mapped_column(String(12), default="general")  # general | elderly
    response_style: Mapped[str] = mapped_column(String(12), default="direct")  # direct | detailed


class FollowUp(Base):
    """'Check in with me': a question to come back to on a given day (from a chat's warning signs or symptoms).
    Shown on Home when due; tapping it asks the assistant."""

    __tablename__ = "follow_ups"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    text_enc: Mapped[bytes] = mapped_column(LargeBinary)
    due_day: Mapped[date] = mapped_column(Date, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class HealthFact(Base):
    """One thing learnt about the person's health, from what they said, a document or a form: a value (101 F,
    BP 140/90), a symptom ("fever", absent: "cough"), a duration, how a medicine worked, a habit… Schema-free:
    `kind` and `name` are open strings and the value is JSON, so new kinds of information need no migration.
    The value is encrypted; kind / name / time stay plain so the timeline can be queried."""

    __tablename__ = "health_facts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)       # vital | symptom | duration | medicine_response | lab | note …
    name: Mapped[str] = mapped_column(String(80), index=True)       # "temperature", "fever", "paracetamol" (lower case)
    value_enc: Mapped[bytes] = mapped_column(LargeBinary)           # JSON: {"value": 101, "unit": "F", "present": true, …}
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    source: Mapped[str] = mapped_column(String(16), default="chat")  # chat | voice | document | manual
    source_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)  # chat session / document id
    episode_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Episode(Base):
    """A health thread that spans hours or days ("fever and body ache, this week"): what was said, measured,
    tried and how it went is linked to it, so later turns ("the temperature is 102 now") and later questions
    ("has this happened before?") connect to it."""

    __tablename__ = "episodes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title_enc: Mapped[bytes] = mapped_column(LargeBinary)
    symptoms: Mapped[list] = mapped_column(JSON, default=list)      # lower-case symptom names (for matching)
    tags_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)  # JSON list: "after the wedding", "monsoon"…
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="open")  # open | closed


class CalendarEvent(Base):
    """An appointment, reminder or note the person put on their care calendar (the title is encrypted)."""

    __tablename__ = "calendar_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(16), default="note")  # appointment | reminder | note
    time: Mapped[str | None] = mapped_column(String(5), nullable=True)  # "HH:MM"
    title_enc: Mapped[bytes] = mapped_column(LargeBinary)
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
    name_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
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
    occupation_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    sleep_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    hydration_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    healthcare_access: Mapped[str | None] = mapped_column(String(40))
    literacy_level: Mapped[str | None] = mapped_column(String(24))
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
    verification_status: Mapped[str] = mapped_column(String(24), default="patient_reported")
    source: Mapped[str] = mapped_column(String(32), default="manual")


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
    verification_status: Mapped[str] = mapped_column(String(24), default="patient_reported")


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
    route: Mapped[str | None] = mapped_column(String(24))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    prescription_item_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(12), default="active")
    reason_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    reported_effectiveness: Mapped[str | None] = mapped_column(String(24))
    adverse_effects_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    prescribed_or_self: Mapped[str] = mapped_column(String(24), default="unknown")
    verification_status: Mapped[str] = mapped_column(String(24), default="patient_reported")
    source: Mapped[str] = mapped_column(String(32), default="manual")


class Vital(Base):
    __tablename__ = "vitals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(16))
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(12))
    measured_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    source: Mapped[str] = mapped_column(String(16), default="user")
    is_estimate: Mapped[bool] = mapped_column(Boolean, default=False)
    original_statement_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
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
    chat_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)  # asked inside a chat


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


class SchemaMigration(Base):
    __tablename__ = "schema_migrations"
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    description: Mapped[str] = mapped_column(String(200))
    applied_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# ---------------------------------------------------------------- longitudinal clinical intelligence
class Encounter(Base):
    """One clinical consultation/interaction session for a patient, persisting across days and restarts."""
    __tablename__ = "encounters"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    chat_session_id: Mapped[str | None] = mapped_column(String(36), index=True)
    episode_id: Mapped[str | None] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | completed | escalated | closed
    channel: Mapped[str] = mapped_column(String(12), default="text")    # voice | text
    language: Mapped[str] = mapped_column(String(8), default="en")
    chief_complaint_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    summary_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    urgency: Mapped[str | None] = mapped_column(String(24))
    specialist: Mapped[str | None] = mapped_column(String(64))
    conversation_state: Mapped[str] = mapped_column(String(32), default="IDLE")
    active_generation_id: Mapped[str | None] = mapped_column(String(64))
    open_fragment_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    interview_outcome: Mapped[str | None] = mapped_column(String(36))
    questions_asked: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    __table_args__ = (Index("ix_encounters_user_started", "user_id", "started_at"),)


class ConversationTurn(Base):
    """Per-turn speech & text record inside an encounter, preserving raw + normalized transcripts."""
    __tablename__ = "conversation_turns"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    encounter_id: Mapped[str] = mapped_column(ForeignKey("encounters.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str | None] = mapped_column(String(36), index=True)
    turn_index: Mapped[int] = mapped_column(Integer, default=0)
    role: Mapped[str] = mapped_column(String(12))  # user | assistant
    raw_transcript_enc: Mapped[bytes] = mapped_column(LargeBinary)
    normalized_transcript_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    detected_lang: Mapped[str] = mapped_column(String(16), default="en")
    code_switched: Mapped[bool] = mapped_column(Boolean, default=False)
    asr_confidence: Mapped[float | None] = mapped_column(Float)
    asr_engine: Mapped[str | None] = mapped_column(String(64))
    speech_segments: Mapped[list | None] = mapped_column(JSON)
    extracted_payload_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class ClinicalSymptom(Base):
    """Persistent longitudinal symptom record for a patient across encounters."""
    __tablename__ = "symptoms"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    episode_id: Mapped[str | None] = mapped_column(String(36), index=True)
    encounter_id: Mapped[str | None] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    normalized_concept: Mapped[str] = mapped_column(String(160))
    anatomical_location: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(16), default="active")        # active|improving|worsening|resolved|unknown
    progression: Mapped[str] = mapped_column(String(16), default="unknown")  # new|worsening|improving|stable|recurrent|resolved|unknown
    onset_text: Mapped[str | None] = mapped_column(String(120))
    estimated_onset: Mapped[datetime | None] = mapped_column(DateTime)
    onset_approximate: Mapped[bool] = mapped_column(Boolean, default=True)
    duration_text: Mapped[str | None] = mapped_column(String(80))
    frequency: Mapped[str | None] = mapped_column(String(80))
    severity: Mapped[str] = mapped_column(String(16), default="unknown")
    character: Mapped[str | None] = mapped_column(String(80))
    functional_impact_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    triggers_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    relieving_factors_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    associated_symptoms_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    original_statement_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    confidence: Mapped[float] = mapped_column(Float, default=0.8)
    verification_status: Mapped[str] = mapped_column(String(24), default="patient_reported")
    source: Mapped[str] = mapped_column(String(32), default="patient_report")
    first_reported_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_reported_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (Index("ix_symptoms_user_status", "user_id", "status"),)


class SymptomObservation(Base):
    """Individual timestamped observation of a symptom (preserves every statement & contradictions)."""
    __tablename__ = "symptom_observations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    symptom_id: Mapped[str] = mapped_column(ForeignKey("symptoms.id", ondelete="CASCADE"), index=True)
    encounter_id: Mapped[str | None] = mapped_column(String(36), index=True)
    turn_id: Mapped[str | None] = mapped_column(String(36))
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    reported_status: Mapped[str] = mapped_column(String(16), default="active")
    progression: Mapped[str] = mapped_column(String(16), default="unknown")
    severity: Mapped[str] = mapped_column(String(16), default="unknown")
    duration_text: Mapped[str | None] = mapped_column(String(80))
    frequency: Mapped[str | None] = mapped_column(String(80))
    triggers_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    relieving_factors_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    functional_impact_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    original_statement_enc: Mapped[bytes] = mapped_column(LargeBinary)
    discrepancy_note_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    is_contradictory: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.8)
    verification_status: Mapped[str] = mapped_column(String(24), default="patient_reported")
    source: Mapped[str] = mapped_column(String(32), default="patient_report")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ClinicalEvent(Base):
    """Append-only auditable clinical timeline event log."""
    __tablename__ = "clinical_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    encounter_id: Mapped[str | None] = mapped_column(String(36), index=True)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(36), index=True)
    source_turn_id: Mapped[str | None] = mapped_column(String(36))
    source: Mapped[str] = mapped_column(String(32), default="patient_report")
    verification_status: Mapped[str] = mapped_column(String(24), default="patient_reported")
    discrepancy_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    payload_enc: Mapped[bytes] = mapped_column(LargeBinary)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    __table_args__ = (Index("ix_clinical_events_user_time", "user_id", "occurred_at"),)


class MedicalHistoryEntry(Base):
    """Structured long-term medical history: diagnoses, surgeries, hospitalizations, evaluations, family history."""
    __tablename__ = "medical_history"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(32), index=True)
    normalized_name: Mapped[str | None] = mapped_column(String(160))
    description_enc: Mapped[bytes] = mapped_column(LargeBinary)
    status: Mapped[str] = mapped_column(String(16), default="historical")
    onset_or_date_text: Mapped[str | None] = mapped_column(String(80))
    verification_status: Mapped[str] = mapped_column(String(24), default="patient_reported")
    source: Mapped[str] = mapped_column(String(32), default="patient_report")
    source_ref: Mapped[str | None] = mapped_column(String(64))
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class FollowUpQuestion(Base):
    """Clinical follow-up questions generated for a specific encounter and patient context."""
    __tablename__ = "followup_questions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    encounter_id: Mapped[str] = mapped_column(ForeignKey("encounters.id", ondelete="CASCADE"), index=True)
    symptom_id: Mapped[str | None] = mapped_column(String(36), index=True)
    question_key: Mapped[str] = mapped_column(String(64), index=True)
    category: Mapped[str] = mapped_column(String(24), default="symptom")
    priority: Mapped[int] = mapped_column(Integer, default=3)
    question_text_enc: Mapped[bytes] = mapped_column(LargeBinary)
    answer_text_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending|answered|skipped|superseded
    reason: Mapped[str | None] = mapped_column(String(200))
    asked_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    __table_args__ = (Index("ix_fq_user_status", "user_id", "status"),)


class PatientMemoryItem(Base):
    """Two-layer patient memory (long_term vs short_term) separating facts from AI hypotheses."""
    __tablename__ = "patient_memory"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    memory_layer: Mapped[str] = mapped_column(String(16), index=True)  # long_term | short_term
    fact_type: Mapped[str] = mapped_column(String(32), index=True)
    topic_key: Mapped[str] = mapped_column(String(120), index=True)
    content_enc: Mapped[bytes] = mapped_column(LargeBinary)
    verification_status: Mapped[str] = mapped_column(String(24), default="patient_reported")
    source: Mapped[str] = mapped_column(String(32), default="patient_report")
    source_ref: Mapped[str | None] = mapped_column(String(64))
    confidence: Mapped[float] = mapped_column(Float, default=0.8)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    superseded_by: Mapped[str | None] = mapped_column(String(36))
    last_accessed_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    __table_args__ = (Index("ix_pm_user_layer_active", "user_id", "memory_layer", "active"),)


class SafetyAlert(Base):
    """Persisted clinical safety / emergency escalation alert."""
    __tablename__ = "safety_alerts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    encounter_id: Mapped[str | None] = mapped_column(String(36), index=True)
    turn_id: Mapped[str | None] = mapped_column(String(36))
    severity: Mapped[str] = mapped_column(String(20))  # emergency | mental_health | urgent
    rule_ids: Mapped[list] = mapped_column(JSON, default=list)
    guidance_enc: Mapped[bytes] = mapped_column(LargeBinary)
    call_numbers: Mapped[list] = mapped_column(JSON, default=list)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    triggered_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class ClinicalRecommendation(Base):
    """Persisted clinical guidance & urgency summary for an encounter."""
    __tablename__ = "clinical_recommendations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    encounter_id: Mapped[str] = mapped_column(ForeignKey("encounters.id", ondelete="CASCADE"), index=True)
    urgency: Mapped[str] = mapped_column(String(24))
    specialist: Mapped[str | None] = mapped_column(String(64))
    summary_enc: Mapped[bytes] = mapped_column(LargeBinary)
    self_care_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    watch_for_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    verification_status: Mapped[str] = mapped_column(String(24), default="ai_hypothesis")
    source: Mapped[str] = mapped_column(String(32), default="assistant")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class Transcript(Base):
    """Canonical transcript entity for one conversation turn, linking raw/corrected versions."""
    __tablename__ = "transcripts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    encounter_id: Mapped[str] = mapped_column(ForeignKey("encounters.id", ondelete="CASCADE"), index=True)
    turn_id: Mapped[str | None] = mapped_column(String(36), index=True)
    speaker: Mapped[str] = mapped_column(String(16), default="patient")  # patient | assistant | caregiver
    raw_text_enc: Mapped[bytes] = mapped_column(LargeBinary)
    corrected_text_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    clinical_interpretation_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    primary_language: Mapped[str] = mapped_column(String(16), default="en")
    detected_languages: Mapped[list | None] = mapped_column(JSON)
    code_switched: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    is_partial: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class TranscriptVersion(Base):
    """Immutable version history of raw recognition, contextual refinement, and edits."""
    __tablename__ = "transcript_versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    transcript_id: Mapped[str] = mapped_column(ForeignKey("transcripts.id", ondelete="CASCADE"), index=True)
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    stage: Mapped[str] = mapped_column(String(32))  # raw_asr | contextual_refinement | user_correction
    text_enc: Mapped[bytes] = mapped_column(LargeBinary)
    model_or_actor: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str | None] = mapped_column(String(200))
    safety_preserved: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ClinicalFact(Base):
    """Normalized atomic clinical fact with exact transcript span, provenance, scope, and correction lineage."""
    __tablename__ = "clinical_facts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    encounter_id: Mapped[str | None] = mapped_column(String(36), index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(36), index=True)
    source_turn_id: Mapped[str | None] = mapped_column(String(36))
    transcript_id: Mapped[str | None] = mapped_column(String(36), index=True)
    scope: Mapped[str] = mapped_column(String(16), default="encounter")  # patient | encounter
    category: Mapped[str] = mapped_column(String(32), index=True)  # complaint|symptom|history|medication|allergy|vital|functional|unknown|contradiction
    fact_key: Mapped[str] = mapped_column(String(120), index=True)
    value_enc: Mapped[bytes] = mapped_column(LargeBinary)
    original_quote_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    span_start: Mapped[int | None] = mapped_column(Integer)
    span_end: Mapped[int | None] = mapped_column(Integer)
    speaker: Mapped[str] = mapped_column(String(16), default="patient")
    temporal_info: Mapped[dict | None] = mapped_column(JSON)
    verification_status: Mapped[str] = mapped_column(String(24), default="PATIENT_REPORTED")  # PATIENT_REPORTED|CLINICIAN_CONFIRMED|MEASURED|IMPORTED_FROM_RECORD|AI_INFERRED
    source_type: Mapped[str] = mapped_column(String(32), default="voice_transcript")
    confidence: Mapped[float] = mapped_column(Float, default=0.85)
    uncertainty_note: Mapped[str | None] = mapped_column(String(200))
    is_contradiction: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    superseded_by: Mapped[str | None] = mapped_column(String(36))
    correction_history: Mapped[list | None] = mapped_column(JSON)
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    __table_args__ = (Index("ix_cf_user_scope_active", "user_id", "scope", "active"),)


class ClinicalAssessment(Base):
    """Evolving clinician-facing / patient-transparent clinical assessment per encounter."""
    __tablename__ = "clinical_assessments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    encounter_id: Mapped[str] = mapped_column(ForeignKey("encounters.id", ondelete="CASCADE"), index=True)
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    summary_enc: Mapped[bytes] = mapped_column(LargeBinary)
    established_facts_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    missing_evidence_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    safety_status: Mapped[str] = mapped_column(String(24), default="routine_uncertain")
    interview_outcome: Mapped[str] = mapped_column(String(36), default="ADDITIONAL_INFORMATION_REQUIRED")
    verification_status: Mapped[str] = mapped_column(String(24), default="AI_INFERRED")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class DifferentialConsideration(Base):
    """Dynamic hypothesis tracked by ClinicalReasoningEngine with for/against/missing evidence."""
    __tablename__ = "differential_considerations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    encounter_id: Mapped[str] = mapped_column(ForeignKey("encounters.id", ondelete="CASCADE"), index=True)
    assessment_id: Mapped[str | None] = mapped_column(ForeignKey("clinical_assessments.id", ondelete="CASCADE"), index=True)
    explanation: Mapped[str] = mapped_column(String(180))
    supporting_evidence_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    against_evidence_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    missing_evidence_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    next_assessment: Mapped[str | None] = mapped_column(String(240))
    likelihood_band: Mapped[str] = mapped_column(String(24), default="possible")
    review_status: Mapped[str] = mapped_column(String(24), default="AI_INFERRED")  # AI_INFERRED | CLINICIAN_CONFIRMED | RULED_OUT
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class ClinicalInvestigation(Base):
    """Patient-dependent investigation consideration matched against existing LabResults."""
    __tablename__ = "clinical_investigations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    encounter_id: Mapped[str] = mapped_column(ForeignKey("encounters.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    purpose_category: Mapped[str] = mapped_column(String(24), default="diagnostic")  # screening | diagnostic | monitoring
    urgency: Mapped[str] = mapped_column(String(16), default="routine")  # urgent | routine
    clinician_decision_required: Mapped[bool] = mapped_column(Boolean, default=True)
    rationale_enc: Mapped[bytes] = mapped_column(LargeBinary)
    supporting_findings_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    limitations_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    already_in_records: Mapped[bool] = mapped_column(Boolean, default=False)
    matched_lab_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class AuditEvent(Base):
    """Patient-scoped immutable audit trail for clinical corrections, state transitions, and provenance."""
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[str] = mapped_column(String(64))
    actor_role: Mapped[str] = mapped_column(String(24), default="patient")  # patient | clinician | system
    action: Mapped[str] = mapped_column(String(48))
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(36), index=True)
    detail_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
