"""One-off: encrypt symptom-check rows saved before triage state was encrypted.

    python -m app.triage.migrate

Idempotent. Runs user by user so Postgres row-level security sees each user's rows.
"""
from __future__ import annotations

import asyncio

from cryptography.exceptions import InvalidTag

from sqlalchemy import delete, select

from app.db import SessionLocal, init_db, scope_to
from app.models import TriageSession, User
from app.security.crypto import Cipher
from app.triage.store import is_encrypted, load_state, save_state


async def encrypt_legacy_rows() -> tuple[int, int, int]:
    """Returns (rows encrypted, rows already encrypted, users whose key this server cannot unwrap)."""
    await init_db()
    done = skipped = foreign = 0
    async with SessionLocal() as db:
        users = list(await db.execute(select(User.id, User.dek_wrapped)))
        if db.bind.dialect.name == "sqlite":  # (Postgres enforces ON DELETE CASCADE itself)
            # rows of deleted accounts: their key is gone, so they can never be read — erase them
            await db.execute(delete(TriageSession).where(TriageSession.user_id.not_in(select(User.id))))
            await db.commit()
    for uid, dek in users:
        async with SessionLocal() as db:
            await scope_to(db, uid)
            try:
                cipher = Cipher(dek, uid)
            except InvalidTag:  # DEK wrapped by a different master key (e.g. rows left by a test run)
                foreign += 1
                continue
            for ts in await db.scalars(select(TriageSession).where(TriageSession.user_id == uid)):
                if is_encrypted(ts.state):
                    skipped += 1
                    continue
                save_state(ts, cipher, load_state(ts, cipher))
                done += 1
            await db.commit()
    return done, skipped, foreign


if __name__ == "__main__":
    n, already, foreign = asyncio.run(encrypt_legacy_rows())
    print(f"encrypted {n} symptom-check rows ({already} were already encrypted); "
          f"skipped {foreign} users whose key belongs to a different master key")
