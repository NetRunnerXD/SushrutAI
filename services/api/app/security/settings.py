"""Per-user settings and the effective privacy policy (offline mode)."""
from __future__ import annotations

import os

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UserSettings
from app.security.consent import active_purposes


def server_offline() -> bool:
    """Operator-level switch: OFFLINE_MODE=1 forces every user to local-only processing."""
    return os.getenv("OFFLINE_MODE", "").lower() in ("1", "true", "yes")


async def get_settings_row(db: AsyncSession, user_id: str) -> UserSettings:
    """Get-or-create, safe when two requests create the row at the same time."""
    s = await db.get(UserSettings, user_id)
    if s is not None:
        return s
    try:
        async with db.begin_nested():
            s = UserSettings(user_id=user_id)
            db.add(s)
        return s
    except IntegrityError:  # another request created it first
        return await db.get(UserSettings, user_id, populate_existing=True)


async def cloud_allowed(db: AsyncSession, user_id: str, purpose: str) -> bool:
    """A cloud call is allowed only with the purpose's consent AND offline mode off (user and server)."""
    if server_offline():
        return False
    s = await db.get(UserSettings, user_id)
    if s is not None and s.offline_mode:
        return False
    return purpose in await active_purposes(db, user_id)
