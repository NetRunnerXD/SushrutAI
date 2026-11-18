"""'Ask about this': turn references to the person's own records into short context for the assistant.

A reference is "<type>:<id>" — lab, med (my medication), product (catalogue medicine), rx (prescription),
vital (a vital type, e.g. vital:bp_sys) or triage (a symptom check). Every lookup is scoped to the active profile
(and decrypted with its key), so a reference can never pull in another person's data.
"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LabResult, Medication, Prescription, Product, TriageSession, Vital
from app.security.auth import CurrentUser
from app.triage import engine
from app.triage.store import load_state

# the kind of question an attached record implies, used when the message itself is vague ("explain this")
HINT = {"lab": "report_question", "doc": "report_question", "vital": "report_question", "med": "medicine", "product": "medicine",
        "rx": "medicine", "triage": "symptoms"}
_VITAL_LABEL = {"bp_sys": "BP systolic", "bp_dia": "BP diastolic", "glucose_f": "fasting sugar",
                "glucose_pp": "after-meal sugar", "glucose_r": "random sugar", "hr": "pulse", "spo2": "SpO2",
                "temp": "temperature", "weight": "weight", "rr": "breathing rate", "waist": "waist"}


def _d(dt) -> str:
    return dt.strftime("%d %b %Y") if dt else "?"


async def _one(db: AsyncSession, cu: CurrentUser, kind: str, rid: str) -> str | None:
    c = cu.cipher
    if kind == "lab":
        lr = await db.get(LabResult, rid)
        if not lr or lr.user_id != cu.id:
            return None
        hist = list(await db.scalars(select(LabResult).where(LabResult.user_id == cu.id,
                                                             LabResult.test_name == lr.test_name)
                                     .order_by(LabResult.observed_at)))
        rng = f", reference {lr.ref_low:g}–{lr.ref_high:g}" if lr.ref_low is not None and lr.ref_high is not None else ""
        flag = {"H": " (HIGH)", "L": " (LOW)", "N": " (normal)"}.get(lr.flag or "", "")
        past = "; earlier: " + ", ".join(f"{h.value_text or h.value} on {_d(h.observed_at)}" for h in hist[:-1][-4:]) if len(hist) > 1 else ""
        return f"Lab result: {lr.test_name} = {lr.value_text or lr.value} {lr.unit or ''}{rng}{flag} on {_d(lr.observed_at)}{past}"
    if kind == "doc":  # a whole lab report: every value read from it (after the person checked them)
        rows = list(await db.scalars(select(LabResult).where(LabResult.user_id == cu.id, LabResult.document_id == rid)))
        if not rows:
            return None
        flag = {"H": " HIGH", "L": " LOW", "N": ""}
        return (f"Lab report of {_d(rows[0].observed_at)}: " + "; ".join(
            f"{r.test_name} {r.value_text or r.value} {r.unit or ''}"
            + (f" (ref {r.ref_low:g}-{r.ref_high:g})" if r.ref_low is not None and r.ref_high is not None else "")
            + flag.get(r.flag or "", "") for r in rows[:30]))
    if kind == "med":
        m = await db.get(Medication, rid)
        if not m or m.user_id != cu.id:
            return None
        f = m.frequency or {}
        sched = f.get("label") or (f"{f.get('morning', 0)}-{f.get('afternoon', 0)}-{f.get('night', 0)}" if f else "")
        return (f"My medicine: {c.dec(m.name_enc)}{' ' + m.dose if m.dose else ''}, {sched} {(m.timing or '').replace('_', ' ')}"
                f"{', since ' + str(m.start_date) if m.start_date else ''}{', until ' + str(m.end_date) if m.end_date else ''}").strip()
    if kind == "product":
        p = await db.get(Product, rid)
        if not p:
            return None
        comp = ", ".join(f"{x.get('generic')} {x.get('strength') or ''}{x.get('unit') or ''}".strip() for x in p.composition or [])
        return f"Medicine: {p.name} ({comp}; {p.form or 'form unknown'}, {p.release_type})"
    if kind == "rx":
        r = await db.get(Prescription, rid)
        if not r or r.user_id != cu.id:
            return None
        parsed = json.loads(c.dec(r.parsed_enc))
        meds = "; ".join(f"{m.get('name')} {m.get('dose') or ''} {m.get('frequency_text') or m.get('frequency') or ''}".strip()
                         for m in parsed.get("medications", []))
        dx = parsed.get("diagnosis")
        dx = ", ".join(map(str, dx)) if isinstance(dx, list) else dx
        return f"My prescription from {_d(r.created_at)}: {meds or 'no medicines read'}" + \
               (f"; diagnosis written: {dx}" if dx else "")
    if kind == "vital":
        rows = list(await db.scalars(select(Vital).where(Vital.user_id == cu.id, Vital.type == rid)
                                     .order_by(Vital.measured_at.desc()).limit(8)))
        if not rows:
            return None
        return f"My {_VITAL_LABEL.get(rid, rid)} readings (newest first): " + ", ".join(
            f"{v.value:g} {v.unit} on {_d(v.measured_at)}" for v in rows)
    if kind == "triage":
        ts = await db.get(TriageSession, rid)
        if not ts or ts.user_id != cu.id:
            return None
        st = load_state(ts, c)
        if st.get("emergency"):
            return f"My symptom check on {_d(ts.created_at)} stopped at an emergency warning."
        ranked = engine.summary(st)[:3] if st.get("conditions") and st.get("posterior") else []
        yes = [f["question"] for f in st.get("findings", []) if st.get("answers", {}).get(f["id"]) == "yes"]
        return (f"My symptom check on {_d(ts.created_at)}: complaint '{st.get('complaint', '')}'"
                + (f"; I answered yes to: {'; '.join(yes)}" if yes else "")
                + (f"; possible causes it listed: {', '.join(r['name'] for r in ranked)}" if ranked else ""))
    return None


async def resolve(db: AsyncSession, cu: CurrentUser, refs: list[str]) -> tuple[str, str | None]:
    """-> (context text for the prompt, implied question kind). Unknown or foreign references are ignored."""
    parts, hint = [], None
    for ref in refs[:3]:
        kind, _, rid = ref.partition(":")
        if kind in HINT and rid and (txt := await _one(db, cu, kind, rid)):
            parts.append(txt)
            hint = hint or HINT[kind]
    return "\n".join(parts), hint
