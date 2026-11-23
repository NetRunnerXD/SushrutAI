"""Medicine trajectory: when a medicine starts, check in after one and two days ("Is it better since starting
X?"). The answer comes back through the chat, where the memory pass stores it as a `medicine_response` fact,
which later answers "what usually works for me?" and appears in medicine storylines."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import local_today
from app.models import FollowUp
from app.security.auth import CurrentUser

CHECK_DAYS = (1, 2)


def start_trajectory(db: AsyncSession, cu: CurrentUser, names: list[str]) -> None:
    """Queue the check-ins (the caller commits). One question per day covers all medicines started together."""
    names = [n for n in dict.fromkeys(n.strip() for n in names) if n][:4]
    if not names:
        return
    what = ", ".join(names)
    for d in CHECK_DAYS:
        text = (f"Since starting {what}: is it better, the same or worse?" if d == 1
                else f"Day {d} of {what}: how are you feeling now — better, the same or worse?")
        db.add(FollowUp(user_id=cu.id, text_enc=cu.cipher.enc(text), due_day=local_today() + timedelta(days=d)))
