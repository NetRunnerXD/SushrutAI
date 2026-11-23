"""The person's health memory: facts linked into episodes, a timeline, and the context the assistant sees.

- Facts from a chat turn are linked to the open episode they belong to ("the temperature is 102 now" joins this
  week's fever), or start a new one when a new problem is described.
- An episode stays open while it is talked about (72 h without mention closes it).
- `memory_block()` gives the assistant the active episode and similar past episodes, so questions such as
  "has this happened before?" or "what usually works for me?" are answered from the person's own history.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.memory.extract import Fact
from app.models import Episode, HealthFact, Vital
from app.security.auth import CurrentUser

OPEN_HOURS = 72          # an episode not mentioned for this long is closed
VITAL_LINKS = {"temperature": {"fever", "high temperature", "chills"}}  # readings that belong to a symptom thread


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)  # naive UTC, like every other table


def _enc(cu: CurrentUser, obj) -> bytes:
    return cu.cipher.enc(json.dumps(obj, ensure_ascii=False))


def _dec(cu: CurrentUser, b: bytes | None):
    return json.loads(cu.cipher.dec(b)) if b else None


async def open_episodes(db: AsyncSession, cu: CurrentUser) -> list[Episode]:
    """Open episodes, closing the ones nobody has mentioned for OPEN_HOURS."""
    eps = list(await db.scalars(select(Episode).where(Episode.user_id == cu.id, Episode.status == "open")
                                .order_by(Episode.last_activity_at.desc())))
    cutoff = _now() - timedelta(hours=OPEN_HOURS)
    alive = []
    for e in eps:
        if e.last_activity_at < cutoff:
            e.status, e.ended_at = "closed", e.last_activity_at
        else:
            alive.append(e)
    return alive


def _overlap(names: set[str], episode_symptoms: list[str]) -> int:
    ep = set(episode_symptoms)
    hits = 0
    for n in names:
        if n in ep or any(n in s or s in n for s in ep):
            hits += 1
    return hits


async def episode_for(db: AsyncSession, cu: CurrentUser, facts: list[Fact]) -> Episode | None:
    """The episode these facts belong to: an open one that shares a symptom (or a reading linked to one of its
    symptoms, or simply the only open one when only readings / a duration were said), else a new one when a
    new symptom is described. None when there is nothing to attach (no symptom, nothing open)."""
    present = {f.name for f in facts if f.kind == "symptom" and f.value.get("present", True)}
    vitals = {f.name for f in facts if f.kind == "vital"}
    eps = await open_episodes(db, cu)
    if present:
        best = max(eps, key=lambda e: _overlap(present, e.symptoms or []), default=None)
        if best and _overlap(present, best.symptoms or []):
            best.symptoms = sorted(set(best.symptoms or []) | present)
            best.last_activity_at = _now()
            return best
        onset = next((f.value.get("onset") for f in facts if f.kind == "duration"), None)
        title = ", ".join(sorted(present)[:3])
        e = Episode(user_id=cu.id, title_enc=cu.cipher.enc(title), symptoms=sorted(present),
                    started_at=datetime.fromisoformat(onset) if onset else _now(), last_activity_at=_now())
        anchors = [f.name for f in facts if f.kind == "context"]
        if anchors:
            e.tags_enc = _enc(cu, anchors)
        db.add(e)
        await db.flush()
        return e
    if eps and (vitals or any(f.kind in ("duration", "medicine_response", "context") for f in facts)):
        for v in vitals:  # "temperature 102" → the open fever episode
            linked = next((e for e in eps if VITAL_LINKS.get(v, set()) & set(e.symptoms or [])), None)
            if linked:
                linked.last_activity_at = _now()
                return linked
        if len(eps) == 1:
            eps[0].last_activity_at = _now()
            return eps[0]
    return None


def _same_value(a: dict, b: dict) -> bool:
    keys = ("value", "amount", "unit", "present", "severity", "effect")  # what makes it the same observation
    return all(a.get(k) == b.get(k) for k in keys)


async def save_facts(db: AsyncSession, cu: CurrentUser, facts: list[Fact], *, source: str = "chat",
                     source_ref: str | None = None, write_vitals: bool = True) -> list[dict]:
    """Store facts (and readings in the Vitals table, so charts keep working); returns what was saved, for the
    "Saving: …" chips the person can undo."""
    if not facts:
        return []
    ep = await episode_for(db, cu, facts)
    saved: list[dict] = []
    now = _now()
    # The same thing said again in the same thread within a few hours (a repeated or re-asked message) is not a
    # new observation: the stored one is shown (and can be undone) instead of a duplicate on the timeline.
    recent = []
    if ep is not None:
        recent = list(await db.scalars(select(HealthFact).where(
            HealthFact.user_id == cu.id, HealthFact.episode_id == ep.id, HealthFact.observed_at >= now - timedelta(hours=6))))
    for f in facts:
        same = next((r for r in recent if r.kind == f.kind and r.name == f.name[:80]
                     and _same_value(_dec(cu, r.value_enc) or {}, f.value)), None)
        if same is not None:
            saved.append({"id": same.id, "kind": f.kind, "name": f.name, "label": f.label(), "value": f.value})
            continue
        row = HealthFact(user_id=cu.id, kind=f.kind, name=f.name[:80], value_enc=_enc(cu, f.value),
                         observed_at=f.observed_at or now, source=source, source_ref=source_ref,
                         episode_id=ep.id if ep else None, confidence=f.confidence)
        db.add(row)
        await db.flush()
        item = {"id": row.id, "kind": f.kind, "name": f.name, "label": f.label(), "value": f.value}
        if write_vitals and f.kind == "vital" and f.value.get("type"):
            v = Vital(user_id=cu.id, type=f.value["type"], value=float(f.value["value"]), unit=f.value.get("unit", ""),
                      measured_at=now, source="voice" if source == "voice" else "chat")
            db.add(v)
            await db.flush()
            item["vital_id"] = v.id
        saved.append(item)
    await db.commit()
    return saved


async def undo_fact(db: AsyncSession, cu: CurrentUser, fact_id: str) -> bool:
    f = await db.get(HealthFact, fact_id)
    if f is None or f.user_id != cu.id:
        return False
    if f.kind == "vital":  # the reading written alongside it (same time, same type)
        val = _dec(cu, f.value_enc) or {}
        v = await db.scalar(select(Vital).where(Vital.user_id == cu.id, Vital.type == val.get("type"),
                                                Vital.measured_at >= f.observed_at - timedelta(seconds=5)).limit(1))
        if v is not None:
            await db.delete(v)
    await db.delete(f)
    await db.commit()
    return True


# ---------------------------------------------------------------- what the assistant sees
def _fact_line(cu: CurrentUser, f: HealthFact) -> str:
    v = _dec(cu, f.value_enc) or {}
    when = f.observed_at.strftime("%d %b")
    if f.kind == "vital":
        return f"{when}: {f.name} {v.get('value')} {v.get('unit', '')}".strip()
    if f.kind == "symptom":
        sev = f" ({v['severity']})" if v.get("severity") not in (None, "unknown") else ""
        trig = f", after {v['trigger']}" if v.get("trigger") else ""
        return f"{when}: {'' if v.get('present', True) else 'no '}{f.name}{sev}{trig}"
    if f.kind == "duration":
        return f"{when}: going on for {v.get('amount')} {v.get('unit')} (since {v.get('onset')})"
    if f.kind == "medicine_response":
        return f"{when}: {f.name} → {v.get('effect')}{(' — ' + v['note']) if v.get('note') else ''}"
    return f"{when}: {f.kind} {f.name}"


async def episode_lines(db: AsyncSession, cu: CurrentUser, e: Episode, limit: int = 12) -> list[str]:
    facts = list(await db.scalars(select(HealthFact).where(HealthFact.episode_id == e.id)
                                  .order_by(HealthFact.observed_at).limit(limit)))
    return [_fact_line(cu, f) for f in facts]


async def memory_block(db: AsyncSession, cu: CurrentUser, text_en: str = "") -> str:
    """The active health thread and similar past episodes, as short lines for the model ("" when empty)."""
    parts: list[str] = []
    eps = await open_episodes(db, cu)
    if eps:
        e = eps[0]
        lines = await episode_lines(db, cu, e)
        parts.append(f"ONGOING (since {e.started_at:%d %b}): {cu.cipher.dec(e.title_enc)}\n" + "\n".join(f"- {l}" for l in lines))
    words = {w for w in re.findall(r"[a-z]{4,}", (text_en or "").lower())}
    past = list(await db.scalars(select(Episode).where(Episode.user_id == cu.id, Episode.status == "closed")
                                 .order_by(Episode.started_at.desc()).limit(30)))
    similar = [e for e in past if words & {w for s in (e.symptoms or []) for w in s.split()}][:3]
    for e in similar:
        lines = await episode_lines(db, cu, e, limit=6)
        tags = _dec(cu, e.tags_enc) or []
        parts.append(f"PAST ({e.started_at:%d %b %Y}{', ' + ', '.join(tags) if tags else ''}): {cu.cipher.dec(e.title_enc)}\n"
                     + "\n".join(f"- {l}" for l in lines))
    docs = await _documents_block(db, cu, words)
    if docs:
        parts.append(docs)
    responses = list(await db.scalars(select(HealthFact).where(HealthFact.user_id == cu.id,
                                                               HealthFact.kind == "medicine_response")
                                      .order_by(HealthFact.observed_at.desc()).limit(5)))
    if responses:
        parts.append("HOW MEDICINES WORKED FOR THEM:\n" + "\n".join(f"- {_fact_line(cu, f)}" for f in responses))
    return "\n\n".join(parts)


# Everyday words → the lab test names they usually mean ("sugar" → glucose / HbA1c)
_LAB_WORDS = {"sugar": ("glucose", "hba1c", "sugar"), "diabetes": ("glucose", "hba1c"), "blood": ("hemoglobin", "haemoglobin", "cbc"),
              "haemoglobin": ("hemoglobin", "haemoglobin"), "hemoglobin": ("hemoglobin", "haemoglobin"),
              "cholesterol": ("cholesterol", "ldl", "hdl", "triglyceride"), "thyroid": ("tsh", "t3", "t4"),
              "kidney": ("creatinine", "urea", "egfr"), "liver": ("sgpt", "sgot", "alt", "ast", "bilirubin"),
              "vitamin": ("vitamin", "b12", "d3"), "platelet": ("platelet",), "report": (), "test": (), "result": ()}
_DOCTOR_WORDS = {"doctor", "prescription", "prescribed", "parcha", "advice", "diagnosis"}


async def _documents_block(db: AsyncSession, cu: CurrentUser, words: set[str]) -> str:
    """Saved lab results and prescriptions that the question is about (voice: "what did the doctor say about my
    sugar last month?", "read the last blood test")."""
    from app.models import LabResult, Prescription

    asked = {w for w in words if w in _LAB_WORDS or w.rstrip("s") in _LAB_WORDS}
    parts: list[str] = []
    if asked:
        names = {n for w in asked for n in _LAB_WORDS.get(w, _LAB_WORDS.get(w.rstrip("s"), ()))}
        labs = list(await db.scalars(select(LabResult).where(LabResult.user_id == cu.id)
                                     .order_by(LabResult.observed_at.desc()).limit(80)))
        pick = [l for l in labs if not names or any(n in l.test_name.lower() for n in names)][:10]
        if pick:
            parts.append("THEIR SAVED TEST RESULTS:\n" + "\n".join(
                f"- {l.observed_at:%d %b %Y}: {l.test_name} {l.value_text or l.value} {l.unit or ''}"
                f"{' (' + {'H': 'high', 'L': 'low', 'N': 'normal'}.get(l.flag or '', l.flag or '') + ')' if l.flag else ''}".rstrip()
                for l in pick))
    if words & _DOCTOR_WORDS:
        for rx in await db.scalars(select(Prescription).where(Prescription.user_id == cu.id)
                                   .order_by(Prescription.created_at.desc()).limit(2)):
            p = json.loads(cu.cipher.dec(rx.parsed_enc))
            meds = ", ".join(m.get("name", "") for m in p.get("medications", []))[:300]
            dx = p.get("diagnosis")
            dx = ", ".join(dx) if isinstance(dx, list) else dx
            extra = "; ".join(x for x in [f"diagnosis: {dx}" if dx else "",
                                          f"tests: {', '.join(p['tests'])}" if p.get("tests") else ""] if x)
            parts.append(f"PRESCRIPTION ({rx.created_at:%d %b %Y}): {meds}{('; ' + extra) if extra else ''}")
    return "\n".join(parts)


async def timeline_items(db: AsyncSession, cu: CurrentUser, limit: int = 200) -> list[dict]:
    """Facts and episodes for the timeline (newest first)."""
    out: list[dict] = []
    for e in await db.scalars(select(Episode).where(Episode.user_id == cu.id).order_by(Episode.started_at.desc()).limit(50)):
        out.append({"type": "episode", "id": e.id, "at": e.started_at, "title": cu.cipher.dec(e.title_enc),
                    "status": e.status, "ended_at": e.ended_at, "symptoms": e.symptoms,
                    "tags": _dec(cu, e.tags_enc) or []})
    for f in await db.scalars(select(HealthFact).where(HealthFact.user_id == cu.id)
                              .order_by(HealthFact.observed_at.desc()).limit(limit)):
        out.append({"type": "fact", "id": f.id, "at": f.observed_at, "kind": f.kind, "name": f.name,
                    "value": _dec(cu, f.value_enc), "episode_id": f.episode_id, "source": f.source,
                    "line": _fact_line(cu, f)})
    out.sort(key=lambda x: x["at"], reverse=True)
    return out
