"""Idempotent versioned schema migrations portable across SQLite and PostgreSQL."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import inspect, text

log = logging.getLogger("sushrut.migrations")

# Column migrations applied idempotently when upgrading existing SQLite/Postgres databases.
_COLUMN_MIGRATIONS: list[tuple[str, str, dict[str, str]]] = [
    ("2026_11_v1_late_columns", "chat_sessions", {
        "title_enc": "BLOB",
        "pinned": "BOOLEAN NOT NULL DEFAULT FALSE",
    }),
    ("2026_11_v1_settings", "user_settings", {
        "model_pref": "VARCHAR(12) NOT NULL DEFAULT 'balanced'",
    }),
    ("2026_11_v1_documents", "documents", {
        "title": "VARCHAR(120)",
        "report_date": "DATE",
        "reviewed_at": "TIMESTAMP",
        "extracted": "JSON",
    }),
    ("2026_11_v1_triage", "triage_sessions", {
        "chat_session_id": "VARCHAR(36)",
    }),
    ("2026_11_v2_clinical_profile", "patient_profiles", {
        "occupation_enc": "BLOB",
        "sleep_enc": "BLOB",
        "hydration_enc": "BLOB",
        "healthcare_access": "VARCHAR(40)",
        "literacy_level": "VARCHAR(24)",
    }),
    ("2026_11_v2_clinical_allergies", "allergies", {
        "verification_status": "VARCHAR(24) NOT NULL DEFAULT 'patient_reported'",
        "source": "VARCHAR(32) NOT NULL DEFAULT 'manual'",
    }),
    ("2026_11_v2_clinical_conditions", "conditions", {
        "verification_status": "VARCHAR(24) NOT NULL DEFAULT 'patient_reported'",
    }),
    ("2026_11_v2_clinical_meds", "medications", {
        "route": "VARCHAR(24)",
        "reason_enc": "BLOB",
        "reported_effectiveness": "VARCHAR(24)",
        "adverse_effects_enc": "BLOB",
        "prescribed_or_self": "VARCHAR(24) NOT NULL DEFAULT 'unknown'",
        "verification_status": "VARCHAR(24) NOT NULL DEFAULT 'patient_reported'",
        "source": "VARCHAR(32) NOT NULL DEFAULT 'manual'",
    }),
    ("2026_11_v2_clinical_vitals", "vitals", {
        "is_estimate": "BOOLEAN NOT NULL DEFAULT FALSE",
        "original_statement_enc": "BLOB",
    }),
    ("2026_11_v3_conversation_state", "encounters", {
        "conversation_state": "VARCHAR(32) NOT NULL DEFAULT 'IDLE'",
        "active_generation_id": "VARCHAR(64)",
        "open_fragment_enc": "BLOB",
        "interview_outcome": "VARCHAR(36)",
        "questions_asked": "INTEGER NOT NULL DEFAULT 0",
    }),
    ("2026_11_v4_patient_name_age", "patient_profiles", {
        "name_enc": "BLOB",
        "age_years": "INTEGER",
    }),
]

_INDEX_MIGRATIONS: list[tuple[str, str, str]] = [
    ("2026_11_v2_idx_meds_user_status", "medications", "CREATE INDEX IF NOT EXISTS ix_meds_user_status ON medications (user_id, status)"),
    ("2026_11_v2_idx_enc_user_time", "encounters", "CREATE INDEX IF NOT EXISTS ix_encounters_user_started ON encounters (user_id, started_at)"),
    ("2026_11_v2_idx_sym_user_status", "symptoms", "CREATE INDEX IF NOT EXISTS ix_symptoms_user_status ON symptoms (user_id, status)"),
    ("2026_11_v2_idx_ce_user_time", "clinical_events", "CREATE INDEX IF NOT EXISTS ix_clinical_events_user_time ON clinical_events (user_id, occurred_at)"),
    ("2026_11_v2_idx_fq_user_status", "followup_questions", "CREATE INDEX IF NOT EXISTS ix_fq_user_status ON followup_questions (user_id, status)"),
    ("2026_11_v2_idx_pm_user_layer", "patient_memory", "CREATE INDEX IF NOT EXISTS ix_pm_user_layer_active ON patient_memory (user_id, memory_layer, active)"),
    ("2026_11_v3_idx_cf_user_scope", "clinical_facts", "CREATE INDEX IF NOT EXISTS ix_cf_user_scope_active ON clinical_facts (user_id, scope, active)"),
]


def run_migrations(sync_conn) -> None:
    """Apply all column and index migrations idempotently and record versions."""
    insp = inspect(sync_conn)
    dialect = sync_conn.dialect.name
    have_migrations = insp.has_table("schema_migrations")
    applied: set[str] = set()
    if have_migrations:
        applied = {r[0] for r in sync_conn.execute(text("SELECT version FROM schema_migrations")).fetchall()}

    for version, table, cols in _COLUMN_MIGRATIONS:
        if not insp.has_table(table):
            continue
        have_cols = {c["name"] for c in insp.get_columns(table)}
        for name, ddl in cols.items():
            if name not in have_cols:
                adj = ddl.replace("BLOB", "BYTEA") if dialect == "postgresql" else ddl
                sync_conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {adj}"))
        if have_migrations and version not in applied:
            sync_conn.execute(
                text("INSERT INTO schema_migrations (version, description, applied_at) VALUES (:v, :d, :t)"),
                {"v": version, "d": f"Add columns to {table}", "t": datetime.now(timezone.utc).replace(tzinfo=None)},
            )
            applied.add(version)

    for version, table, sql in _INDEX_MIGRATIONS:
        if not insp.has_table(table):
            continue
        if version not in applied:
            sync_conn.execute(text(sql))
            if have_migrations:
                sync_conn.execute(
                    text("INSERT INTO schema_migrations (version, description, applied_at) VALUES (:v, :d, :t)"),
                    {"v": version, "d": f"Create index on {table}", "t": datetime.now(timezone.utc).replace(tzinfo=None)},
                )
                applied.add(version)
