"""Symptom-check state at rest: the complaint, answers and question plan are health data, so the whole state is
encrypted with the user's own key (same envelope encryption as every other PHI field)."""
from __future__ import annotations

import base64
import json

from sqlalchemy.orm.attributes import flag_modified

from app.models import TriageSession
from app.security.crypto import Cipher

FORMAT = "enc1"


def is_encrypted(raw: dict | None) -> bool:
    return isinstance(raw, dict) and raw.get("v") == FORMAT and "data" in raw


def load_state(ts: TriageSession, cipher: Cipher) -> dict:
    raw = ts.state
    if is_encrypted(raw):
        return json.loads(cipher.dec(base64.b64decode(raw["data"])))
    return dict(raw or {})  # rows written before encryption: read as-is, re-encrypted by save_state


def save_state(ts: TriageSession, cipher: Cipher, state: dict) -> None:
    blob = cipher.enc(json.dumps(state, ensure_ascii=False, default=str))
    ts.state = {"v": FORMAT, "data": base64.b64encode(blob).decode()}
    flag_modified(ts, "state")
