"""The user's calendar day. People log doses and check-ins by their own clock, not UTC: between midnight and
05:30 in India, UTC is still on the previous day. India has one time zone and no daylight saving, so a fixed
offset is exact (and needs no tz database, which Windows lacks). APP_UTC_OFFSET_MINUTES overrides it."""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

TZ = timezone(timedelta(minutes=int(os.getenv("APP_UTC_OFFSET_MINUTES", "330"))))


def local_now() -> datetime:
    return datetime.now(TZ)


def local_today() -> date:
    return local_now().date()
