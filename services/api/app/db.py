import os
from collections.abc import AsyncIterator
from contextvars import ContextVar

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session
from sqlalchemy.pool import NullPool

from app.config import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()
_pool = {"poolclass": NullPool} if os.getenv("DB_POOL") == "null" else {"pool_pre_ping": True}
engine = create_async_engine(_settings.database_url, future=True, **_pool)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
IS_POSTGRES = engine.dialect.name == "postgresql"


if engine.dialect.name == "sqlite":
    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_fk(dbapi_conn, _record):  # noqa: ANN001
        # SQLite ignores FOREIGN KEY ... ON DELETE CASCADE unless asked: without this, deleting a user could
        # leave health rows behind.
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

# The authenticated user for the current request / background task (asyncio tasks copy the context).
current_user_id: ContextVar[str | None] = ContextVar("current_user_id", default=None)
# The account holder (guardian). Equals current_user_id except when a family member's profile is active.
current_account_id: ContextVar[str | None] = ContextVar("current_account_id", default=None)

# Tables protected by Postgres row-level security: a row is visible only when its user_id equals the
# per-transaction setting app.user_id. Auth/lookup tables (users, otp_challenges) and public reference data
# (drug_*, kb_chunks) are not row-scoped.
RLS_TABLES = ["patient_profiles", "allergies", "conditions", "medications", "vitals", "documents", "jobs",
              "prescriptions", "chat_sessions", "triage_sessions", "lab_results", "user_settings", "consents", "dose_logs",
              "household_members", "checkins", "care_records", "pregnancies", "visit_questions", "follow_ups",
              "calendar_events", "health_facts", "episodes",
              "encounters", "conversation_turns", "symptoms", "symptom_observations", "clinical_events",
              "medical_history", "followup_questions", "patient_memory", "safety_alerts", "clinical_recommendations",
              "transcripts", "transcript_versions", "clinical_facts", "clinical_assessments",
              "differential_considerations", "clinical_investigations", "audit_events"]
# Account-level rows (the guardian's consents and privacy settings, and the family list) stay visible while a
# family member's profile is active.
ACCOUNT_TABLES = {"user_settings", "consents", "household_members"}


@event.listens_for(Session, "after_begin")
def _set_rls_user(session, transaction, connection):  # noqa: ANN001
    if connection.dialect.name == "postgresql":
        uid = current_user_id.get() or ""
        connection.execute(text("SELECT set_config('app.user_id', :uid, true), set_config('app.account_id', :aid, true)"),
                           {"uid": uid, "aid": current_account_id.get() or uid})


async def scope_to(db: AsyncSession, user_id: str, account_id: str | None = None) -> None:
    """Switch the row-level-security scope mid-request (used when acting on a family member's data)."""
    current_user_id.set(user_id)
    current_account_id.set(account_id or user_id)
    if db.in_transaction() and db.bind.dialect.name == "postgresql":
        await db.execute(text("SELECT set_config('app.user_id', :uid, true), set_config('app.account_id', :aid, true)"),
                         {"uid": user_id, "aid": account_id or user_id})


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


# Columns added after a table first shipped: create_all() does not alter existing tables.
_LATE_COLUMNS = {
    "chat_sessions": {"title_enc": "BLOB", "pinned": "BOOLEAN NOT NULL DEFAULT FALSE"},
    "user_settings": {"model_pref": "VARCHAR(12) NOT NULL DEFAULT 'balanced'"},
    "documents": {"title": "VARCHAR(120)", "report_date": "DATE", "reviewed_at": "TIMESTAMP", "extracted": "JSON"},
    "triage_sessions": {"chat_session_id": "VARCHAR(36)"},
}


def _add_missing_columns(sync_conn) -> None:
    from sqlalchemy import inspect

    insp = inspect(sync_conn)
    for table, cols in _LATE_COLUMNS.items():
        if not insp.has_table(table):
            continue
        have = {c["name"] for c in insp.get_columns(table)}
        for name, ddl in cols.items():
            if name not in have:
                if sync_conn.dialect.name == "postgresql":
                    ddl = ddl.replace("BLOB", "BYTEA")
                sync_conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


async def init_db() -> None:
    from app import models  # noqa: F401  (register tables)
    from app.migrations import run_migrations

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(run_migrations)
        if IS_POSTGRES:
            for t in RLS_TABLES:
                await conn.execute(text(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY"))
                await conn.execute(text(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY"))
                await conn.execute(text(f"DROP POLICY IF EXISTS user_isolation ON {t}"))
                who = ("user_id IN (current_setting('app.user_id', true), current_setting('app.account_id', true))"
                       if t in ACCOUNT_TABLES else "user_id = current_setting('app.user_id', true)")
                await conn.execute(text(f"CREATE POLICY user_isolation ON {t} USING ({who}) WITH CHECK ({who})"))
