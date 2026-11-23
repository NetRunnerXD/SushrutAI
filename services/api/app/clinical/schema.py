"""Strict Pydantic v2 schemas for structured clinical information extraction.

Enforces clinical data integrity rules:
- Preserve original patient statements verbatim.
- Maintain explicit 'unknown' / 'not_assessed' states; never convert unknown to negative.
- Separate patient-reported facts from clinician-confirmed facts and AI hypotheses.
- Never invent missing demographics, onset dates, or unidentified medications.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

SeverityLevel = Literal["mild", "moderate", "severe", "unknown", "not_assessed"]
ProgressionState = Literal["new", "worsening", "improving", "stable", "recurrent", "resolved", "unknown"]
SymptomStatus = Literal["active", "improving", "worsening", "resolved", "unknown"]
VerificationStatus = Literal[
    "unverified",
    "patient_reported",
    "clinician_reported",
    "clinician_confirmed",
    "ai_hypothesis",
]
SourceType = Literal[
    "patient_report",
    "voice_transcript",
    "chat_text",
    "document_ocr",
    "clinician_entry",
    "device_measurement",
    "patient_estimate",
    "manual",
]
PrescribedStatus = Literal["prescribed", "self_administered", "unknown"]
EffectivenessStatus = Literal["better", "same", "worse", "side_effect", "unknown", "not_assessed"]


class TemporalInfo(BaseModel):
    reported_onset: str | None = None
    estimated_onset: str | None = None  # ISO-8601 date/datetime ONLY when sufficiently known
    exact_observation_time: str | None = None  # ISO-8601 when reported
    duration_text: str | None = None
    duration_days: float | None = None
    frequency: str | None = None  # intermittent | continuous | postprandial | nocturnal | etc.
    progression: ProgressionState = "unknown"
    last_occurrence: str | None = None
    is_approximate: bool = False
    is_temporal_uncertain: bool = True


class PatientInfoExtracted(BaseModel):
    age_years: float | None = Field(default=None, ge=0, le=125)
    dob: str | None = None
    preferred_language: str | None = None
    occupation: str | None = None
    lifestyle_notes: list[str] = Field(default_factory=list)
    known_diagnoses: list[str] = Field(default_factory=list)
    family_history: list[str] = Field(default_factory=list)


class SymptomExtracted(BaseModel):
    symptom_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    original_statement: str = Field(min_length=1, max_length=600)
    normalized_description: str = Field(min_length=1, max_length=300)
    anatomical_location: str | None = None

    onset: str | None = None
    duration: str | None = None
    frequency: str | None = None
    severity: SeverityLevel = "unknown"
    character: str | None = None

    triggers: list[str] = Field(default_factory=list)
    relieving_factors: list[str] = Field(default_factory=list)
    associated_symptoms: list[str] = Field(default_factory=list)
    functional_impact: str | None = None

    status: SymptomStatus = "active"
    progression: ProgressionState = "unknown"
    present: bool | None = True  # False ONLY for explicit negative findings; None = unknown

    temporal: TemporalInfo = Field(default_factory=TemporalInfo)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    verification_status: VerificationStatus = "patient_reported"
    source: SourceType = "patient_report"

    @field_validator("name")
    @classmethod
    def _clean_name(cls, v: str) -> str:
        return " ".join(v.strip().lower().split())[:120]

    @field_validator("triggers", "relieving_factors", "associated_symptoms", mode="before")
    @classmethod
    def _clean_list(cls, v):
        if not v:
            return []
        if isinstance(v, str):
            v = [v]
        empty = {"unknown", "none", "n/a", "na", "not stated", "not mentioned", "nothing", "null", ""}
        return [str(x).strip() for x in v if str(x).strip().lower() not in empty][:12]


class MedicationExtracted(BaseModel):
    medication_name_reported: str = Field(min_length=1, max_length=200)
    normalized_medication_name: str | None = None  # Never guessed from vague visual description
    unidentified: bool = False
    dosage: str | None = None
    frequency: str | None = None
    route: str | None = None
    start_date: str | None = None
    stop_date: str | None = None
    duration: str | None = None
    reason_for_use: str | None = None
    reported_effectiveness: EffectivenessStatus = "not_assessed"
    adverse_effects: list[str] = Field(default_factory=list)
    prescribed_or_self: PrescribedStatus = "unknown"
    needs_photo_or_pharmacist: bool = False
    original_statement: str = Field(min_length=1, max_length=600)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    verification_status: VerificationStatus = "patient_reported"
    source: SourceType = "patient_report"


class AllergyExtracted(BaseModel):
    substance: str = Field(min_length=1, max_length=120)
    allergy_type: Literal["drug", "food", "other", "unknown"] = "unknown"
    reaction: str | None = None
    severity: Literal["mild", "moderate", "severe", "anaphylaxis", "unknown"] = "unknown"
    original_statement: str = Field(min_length=1, max_length=400)
    verification_status: VerificationStatus = "patient_reported"
    source: SourceType = "patient_report"


class MedicalHistoryExtracted(BaseModel):
    category: Literal[
        "diagnosis",
        "surgery",
        "hospitalization",
        "chronic_condition",
        "previous_symptom",
        "evaluation",
        "family_history",
    ]
    description: str = Field(min_length=1, max_length=300)
    normalized_name: str | None = None
    status: Literal["active", "resolved", "historical", "unknown"] = "historical"
    date_or_period: str | None = None
    original_statement: str = Field(min_length=1, max_length=400)
    verification_status: VerificationStatus = "patient_reported"
    source: SourceType = "patient_report"


class VitalMeasurementExtracted(BaseModel):
    vital_type: Literal[
        "bp_sys",
        "bp_dia",
        "hr",
        "temp",
        "spo2",
        "rr",
        "glucose_f",
        "glucose_pp",
        "glucose_r",
        "weight",
        "height",
    ]
    value: float
    unit: str = Field(min_length=1, max_length=20)
    measured_at: str  # ISO-8601
    source: SourceType = "patient_report"
    is_estimate: bool = False  # Distinguish measured reading vs patient guess ("feels like 102")
    original_statement: str = Field(min_length=1, max_length=300)


class FunctionalSocialContext(BaseModel):
    occupation: str | None = None
    physical_activity: str | None = None
    diet: str | None = None
    sleep: str | None = None
    hydration: str | None = None
    smoking_tobacco: str | None = None
    alcohol: str | None = None
    healthcare_access: str | None = None
    written_literacy: Literal["fluent", "limited", "non_reader", "not_assessed"] = "not_assessed"


class SafetyFlagExtracted(BaseModel):
    rule_id: str
    severity: Literal["emergency", "mental_health", "urgent", "caution"]
    description: str
    evidence: str
    status: Literal["positive", "negative_reported", "unknown", "not_assessed"] = "positive"


class MissingInfoItem(BaseModel):
    field: str
    category: str
    priority: int = Field(ge=1, le=5)
    reason: str
    target_symptom: str | None = None


class ClinicalExtractionPayload(BaseModel):
    patient_id: str
    encounter_id: str
    language: str = "en"
    extraction_timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    raw_transcript: str = ""
    normalized_transcript: str = ""

    patient_info: PatientInfoExtracted = Field(default_factory=PatientInfoExtracted)
    symptoms: list[SymptomExtracted] = Field(default_factory=list)
    negated_symptoms: list[SymptomExtracted] = Field(default_factory=list)
    medications: list[MedicationExtracted] = Field(default_factory=list)
    allergies: list[AllergyExtracted] = Field(default_factory=list)
    medical_history: list[MedicalHistoryExtracted] = Field(default_factory=list)
    vital_measurements: list[VitalMeasurementExtracted] = Field(default_factory=list)
    functional_context: FunctionalSocialContext = Field(default_factory=FunctionalSocialContext)

    missing_information: list[MissingInfoItem] = Field(default_factory=list)
    safety_flags: list[SafetyFlagExtracted] = Field(default_factory=list)
    follow_up_candidates: list[str] = Field(default_factory=list)
    validation_warnings: list[str] = Field(default_factory=list)
