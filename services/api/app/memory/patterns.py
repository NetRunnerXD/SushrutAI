"""Gentle patterns from the person's own memory, counted, never diagnosed.

- a symptom mentioned on 3+ different days in 30 days, with the trigger they named most often;
- how a medicine went each time it was mentioned (better / same / worse).
No disease or symptom list is hard-coded: everything comes from the stored facts.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import HealthFact
from app.security.auth import CurrentUser

MIN_DAYS = 3
WINDOW_DAYS = 30


async def insights(db: AsyncSession, cu: CurrentUser) -> list[dict]:
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=WINDOW_DAYS)
    facts = list(await db.scalars(select(HealthFact).where(HealthFact.user_id == cu.id, HealthFact.observed_at >= since,
                                                           HealthFact.kind.in_(["symptom", "medicine_response"]))))
    days: dict[str, set] = defaultdict(set)
    triggers: dict[str, Counter] = defaultdict(Counter)
    responses: dict[str, Counter] = defaultdict(Counter)
    for f in facts:
        v = json.loads(cu.cipher.dec(f.value_enc))
        if f.kind == "symptom" and v.get("present", True):
            days[f.name].add(f.observed_at.date())
            if v.get("trigger"):
                triggers[f.name][v["trigger"]] += 1
        elif f.kind == "medicine_response" and v.get("effect"):
            responses[f.name][v["effect"]] += 1
    out: list[dict] = []
    for name, ds in sorted(days.items(), key=lambda kv: -len(kv[1])):
        if len(ds) >= MIN_DAYS:
            trig = triggers[name].most_common(1)
            out.append({"type": "recurring", "symptom": name, "times": len(ds),
                        "trigger": trig[0][0] if trig and trig[0][1] >= 2 else None,
                        "say": f"You have mentioned {name} on {len(ds)} different days this month"
                               + (f", usually {trig[0][0]}" if trig and trig[0][1] >= 2 else "") + "."})
    for med, c in responses.items():
        effect, n = c.most_common(1)[0]
        out.append({"type": "medicine_response", "medicine": med, "effect": effect, "times": sum(c.values()),
                    "say": f"When you took {med}, it was mostly {effect.replace('_', ' ')} ({sum(c.values())} times noted)."})
    return out
