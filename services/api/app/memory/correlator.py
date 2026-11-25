"""Low-latency Cross-Table, Temporal & Family Correlation Engine.

Combines:
1. Medication <-> Symptom attribution (known drug-class side effects & interactions)
2. Lab-trend <-> Symptom correlation (temporal slope & threshold crossings)
3. Privacy-preserving Household / Family correlation (hereditary risks & 14-day shared exposure)
4. Lightweight 2-hop Personalized PageRank (GraphRanker) over the patient's heterogeneous clinical graph (< 5 ms CPU).
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ClinicalSymptom, Condition, HouseholdMember, LabResult, User
from app.security.crypto import Cipher

_DRUG_SYMPTOM_EDGES: tuple[tuple[tuple[str, ...], tuple[str, ...], str], ...] = (
    (("enalapril", "ramipril", "lisinopril", "perindopril"), ("cough", "dry cough", "কাশি", "खांसी"),
     "ACE-inhibitor blood pressure medicines can cause a persistent dry cough."),
    (("metformin",), ("nausea", "diarrhea", "acidity", "tingling", "numbness", "weakness"),
     "Metformin can contribute to GI upset or lower Vitamin B12 over long-term use."),
    (("glimepiride", "gliclazide", "insulin", "glipizide"), ("dizziness", "sweating", "shakiness", "weakness"),
     "Blood-sugar-lowering medicines can trigger hypoglycaemia if meals are delayed."),
    (("ibuprofen", "diclofenac", "aceclofenac", "naproxen", "aspirin"), ("acidity", "abdominal pain", "nausea", "black stool"),
     "NSAID pain relievers can irritate the stomach lining or stress kidney filtration."),
    (("amlodipine", "nifedipine"), ("swelling", "swollen ankles", "dizziness", "headache"),
     "Calcium-channel blockers like amlodipine commonly cause ankle swelling or mild flushing."),
    (("atenolol", "metoprolol", "propranolol"), ("fatigue", "dizziness", "weakness", "breathless"),
     "Beta-blockers can lower heart rate/BP and contribute to fatigue or lightheadedness."),
)

_HEREDITARY_KEYWORDS = ("diabetes", "hypertension", "bp", "heart", "coronary", "asthma", "thyroid", "migraine", "kidney", "cancer")
_EXPOSURE_KEYWORDS = ("fever", "cough", "cold", "rash", "dengue", "viral", "diarrhea", "vomiting", "jaundice", "জ্বর", "बुखार")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _dec(cipher: Cipher, blob: bytes | None) -> str:
    if not blob:
        return ""
    try:
        return cipher.dec(blob)
    except Exception:  # noqa: BLE001
        return ""


class GraphRanker:
    """Lightweight 2-hop Personalized PageRank over a patient's heterogeneous clinical graph."""

    def rank(self, seed_nodes: list[str], edges: list[tuple[str, str, float]], *, top_k: int = 6) -> list[tuple[str, float]]:
        if not seed_nodes or not edges:
            return []
        adj: dict[str, list[tuple[str, float]]] = {}
        nodes: set[str] = set(seed_nodes)
        for u, v, w in edges:
            nodes.add(u)
            nodes.add(v)
            adj.setdefault(u, []).append((v, w))
            adj.setdefault(v, []).append((u, w))

        n = len(nodes)
        if n == 0:
            return []
        scores: dict[str, float] = {k: 0.0 for k in nodes}
        seed_w = 1.0 / len(seed_nodes)
        for s in seed_nodes:
            scores[s] = seed_w

        alpha = 0.85
        for _ in range(2):
            nxt: dict[str, float] = {k: (1.0 - alpha) * (seed_w if k in seed_nodes else 0.0) for k in nodes}
            for u, sc in scores.items():
                nbrs = adj.get(u)
                if not nbrs or sc <= 0:
                    continue
                tot = sum(w for _, w in nbrs) or 1.0
                for v, w in nbrs:
                    nxt[v] += alpha * sc * (w / tot)
            scores = nxt

        ranked = [(k, round(v, 4)) for k, v in scores.items() if k not in seed_nodes and v > 0]
        ranked.sort(key=lambda x: x[1], reverse=True)
        return ranked[:top_k]


class ClinicalCorrelator:
    """Correlates medications, lab trends, symptoms, and anonymized household signals."""

    def correlate_meds_and_symptoms(self, meds: list[dict], symptoms: list[str]) -> list[str]:
        med_names = [str(m.get("name") or "").lower() for m in meds if m.get("name")]
        sym_text = " ".join(s.lower() for s in symptoms)
        out: list[str] = []
        for drugs, cues, note in _DRUG_SYMPTOM_EDGES:
            if any(d in mn for mn in med_names for d in drugs) and any(c in sym_text for c in cues):
                out.append(note)
        return out[:3]

    def correlate_labs(self, labs: list[LabResult]) -> list[str]:
        by_name: dict[str, list[LabResult]] = {}
        for lr in labs:
            if lr.value is None:
                continue
            by_name.setdefault(lr.test_name.lower(), []).append(lr)
        notes: list[str] = []
        for name, rows in by_name.items():
            rows.sort(key=lambda r: r.observed_at)
            if len(rows) >= 2:
                first, last = rows[0], rows[-1]
                delta = round(float(last.value) - float(first.value), 2)
                days = max(1, (last.observed_at - first.observed_at).days)
                decay = math.exp(-days / 180.0)
                if abs(delta) >= 0.4 and decay > 0.15:
                    direction = "rising" if delta > 0 else "falling"
                    notes.append(f"{last.test_name} trend is {direction} ({first.value} -> {last.value} {last.unit or ''} over {days}d).")
            elif rows[-1].flag in ("H", "L"):
                lr = rows[-1]
                flag_txt = "high" if lr.flag == "H" else "low"
                notes.append(f"Recent {lr.test_name} was {flag_txt} ({lr.value} {lr.unit or ''}).")
        return notes[:4]

    async def correlate_household(self, db: AsyncSession, account_id: str, active_user_id: str) -> list[str]:
        """Privacy-preserving household correlation: hereditary conditions & 14-day shared exposure (no names)."""
        links = list(await db.scalars(select(HouseholdMember).where(HouseholdMember.user_id == account_id)))
        if not links:
            return []
        member_ids = [lk.member_id for lk in links if lk.member_id != active_user_id]
        if account_id != active_user_id:
            member_ids.append(account_id)
        if not member_ids:
            return []

        cutoff = _now() - timedelta(days=14)
        hereditary: set[str] = set()
        exposures: set[str] = set()

        for mid in member_ids[:6]:
            m_user = await db.get(User, mid)
            if not m_user:
                continue
            mcipher = Cipher(m_user.dek_wrapped, m_user.id)
            conds = list(await db.scalars(select(Condition).where(Condition.user_id == mid).limit(10)))
            for c in conds:
                txt = _dec(mcipher, c.display_enc).lower()
                for kw in _HEREDITARY_KEYWORDS:
                    if kw in txt:
                        hereditary.add(kw)
            syms = list(await db.scalars(
                select(ClinicalSymptom).where(ClinicalSymptom.user_id == mid, ClinicalSymptom.last_reported_at >= cutoff).limit(10)
            ))
            for s in syms:
                txt = (s.name or "").lower()
                for kw in _EXPOSURE_KEYWORDS:
                    if kw in txt:
                        exposures.add(kw)

        out: list[str] = []
        if exposures:
            out.append(f"Recent household exposure (past 14 days): {', '.join(sorted(exposures)[:3])}.")
        if hereditary:
            out.append(f"Family history in household: {', '.join(sorted(hereditary)[:3])}.")
        return out
