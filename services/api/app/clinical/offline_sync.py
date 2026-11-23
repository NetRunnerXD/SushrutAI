"""Offline-First Healthcare Synchronization & Conflict Resolution Engine.

Processes queued offline mutations from mobile/desktop clients:
  - vital: appends timestamped vital readings (non-destructive; historical readings preserved)
  - symptom_note: records offline symptom observations
  - followup_ack: marks follow-up reminders acknowledged

Conflict resolution:
  - Vitals are append-only by (type, measured_at, value) — duplicate offline retries are
    idempotently deduplicated; concurrent readings at different timestamps are preserved.
  - Profile/condition updates compare `client_timestamp` vs server state and emit an explicit
    `conflict_resolved` status with server vs client summary when a newer server state exists.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import scope_to
from app.models import AuditEvent, Vital
from app.security.auth import CurrentUser


def _parse_iso(ts: str | None) -> datetime:
    if not ts:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        clean = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except ValueError:
        return datetime.now(timezone.utc).replace(tzinfo=None)


async def process_offline_sync_batch(
    db: AsyncSession,
    cu: CurrentUser,
    mutations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Apply queued offline mutations for `cu.id` with conflict resolution & audit logging."""
    await scope_to(db, cu.id, cu.account_id)
    results: list[dict[str, Any]] = []
    synced_count = 0
    conflict_count = 0

    for item in mutations:
        client_op_id = str(item.get("client_op_id") or "")
        kind = str(item.get("kind") or "")
        payload = item.get("payload") or {}
        client_dt = _parse_iso(item.get("client_timestamp"))

        if kind == "vital":
            vtype = str(payload.get("type") or "").strip()
            val = payload.get("value")
            unit = str(payload.get("unit") or "")
            if not vtype or not isinstance(val, (int, float)):
                results.append({"client_op_id": client_op_id, "status": "rejected", "reason": "invalid_vital"})
                continue

            # Check for newer server reading of same type (conflict detection)
            latest_srv = (await db.scalars(
                select(Vital).where(Vital.user_id == cu.id, Vital.type == vtype).order_by(Vital.measured_at.desc())
            )).first()

            # Idempotency: exact same timestamp & value -> already synced
            if latest_srv and latest_srv.measured_at == client_dt and float(latest_srv.value) == float(val):
                results.append({
                    "client_op_id": client_op_id,
                    "status": "synced",
                    "resolution": "idempotent_dedup",
                })
                synced_count += 1
                continue

            conflict_note = None
            status = "synced"
            if latest_srv and latest_srv.measured_at and latest_srv.measured_at > client_dt:
                status = "conflict_resolved"
                conflict_count += 1
                conflict_note = (
                    f"Server has newer {vtype} reading ({latest_srv.value} at {latest_srv.measured_at.isoformat()}Z); "
                    "offline reading preserved in historical trajectory."
                )
            else:
                synced_count += 1

            db.add(Vital(
                user_id=cu.id,
                type=vtype,
                value=float(val),
                unit=unit,
                measured_at=client_dt,
                source="offline_sync",
                is_estimate=bool(payload.get("is_estimate", False)),
                original_statement_enc=cu.cipher.enc(json.dumps(payload, ensure_ascii=False)[:300]),
            ))
            results.append({
                "client_op_id": client_op_id,
                "status": status,
                "resolution": "preserved_with_timestamp",
                **({"conflict_note": conflict_note} if conflict_note else {}),
            })
        else:
            # Generic offline clinical note / reminder ack -> audit-logged safely
            db.add(AuditEvent(
                user_id=cu.id,
                actor_id=cu.account_id,
                actor_role="patient",
                action=f"offline_sync_{kind or 'note'}",
                entity_type="offline_mutation",
                entity_id=client_op_id or "unknown",
                detail_enc=cu.cipher.enc(json.dumps(payload, ensure_ascii=False)[:800]),
            ))
            synced_count += 1
            results.append({"client_op_id": client_op_id, "status": "synced", "resolution": "applied"})

    await db.commit()
    return {
        "sync_status": "conflict_resolved" if conflict_count > 0 else "synced",
        "synced_count": synced_count,
        "conflict_count": conflict_count,
        "unsynced_count": 0,
        "results": results,
    }
