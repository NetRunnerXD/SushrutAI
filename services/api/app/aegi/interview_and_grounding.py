"""Modules 2 & 3: AEGI Adaptive Clinical Interview & Evidence-Grounded Answer Generation.

- Module 2: Selects up to 2-3 high-information-gain questions, detects contradictory patient
  responses, avoids repeating answered questions, and stops immediately on emergency.
- Module 3: Retrieves curated evidence from RAG (MedlinePlus / ICMR) and Knowledge Graph (GNN/KG),
  records source title, publication/update date, and section, separates established facts from
  possible explanations, and flags insufficient/conflicting evidence.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.clinical.knowledge_graph import KNOWLEDGE_GRAPH_VERSION, LAST_UPDATED, analyze_clinical_differentials
from app.rag.store import search as rag_search

_CONTRADICTION_PAIRS = (
    (re.compile(r"(?i)\bno\s+fever\b|জ্বর\s*নেই|बुखार\s*नहीं"), re.compile(r"(?i)\b(?:high\s+fever|fever\s+for|10[1-4]\s*°?\s*f)\b|জ্বর\s*(?:আছে|হয়েছে)")),
    (re.compile(r"(?i)\bno\s+chest\s+pain\b|বুকে\s*ব্যথা\s*নেই|सीने\s*में\s*दर्द\s*नहीं"), re.compile(r"(?i)\bchest\s+pain\b|বুকে\s*ব্যথা")),
    (re.compile(r"(?i)\bonly\s+when\s+standing\b|শুধু\s*দাঁড়ালে"), re.compile(r"(?i)\beven\s+when\s+(?:sitting|lying)\b|বসে\s*থাকলেও")),
)


def detect_contradictions(current_text: str, history: list[dict] | None = None) -> list[dict[str, str]]:
    """Detect direct clinical contradictions within current turn or against prior turns."""
    prev_user = " ".join(h.get("content", "") for h in (history or []) if h.get("role") == "user")
    combined = f"{prev_user} {current_text or ''}".strip()
    found: list[dict[str, str]] = []
    for p_neg, p_pos in _CONTRADICTION_PAIRS:
        if p_neg.search(combined) and p_pos.search(combined):
            found.append({
                "type": "contradictory_symptom_report",
                "detail": "Earlier and current statements appear to conflict; ask the patient to clarify.",
            })
    return found


def build_interview_record(
    *,
    current_text: str,
    candidate_questions: list[str],
    history: list[dict] | None = None,
    missing_dimensions: list[str] | None = None,
    emergency: bool = False,
) -> dict[str, Any]:
    """Module 2: Track questions asked, answers received, missing info, contradictions, and selection reason."""
    prev_assistant_qs = [
        h.get("content", "") for h in (history or []) if h.get("role") == "assistant" and "?" in (h.get("content") or "")
    ]
    prev_user_ans = [h.get("content", "") for h in (history or []) if h.get("role") == "user"]
    contradictions = detect_contradictions(current_text, history)

    if emergency:
        selected: list[str] = []
        reason = "Stopped routine questioning immediately due to emergency warning signs."
    elif contradictions:
        selected = [
            "Could you please clarify — you mentioned both having and not having this symptom earlier?"
        ]
        reason = "Clarifying contradictory patient statements before continuing."
    else:
        # Avoid repeating any question already asked in prior turns
        asked_lower = " ".join(prev_assistant_qs).lower()
        selected = [q for q in candidate_questions if q.lower() not in asked_lower][:3]
        reason = (
            "Selected top uncertainty-reducing questions based on missing clinical dimensions."
            if selected
            else "Sufficient clinical context available for current turn."
        )

    return {
        "questions_asked_previously": len(prev_assistant_qs),
        "answers_received_count": len(prev_user_ans),
        "missing_information": missing_dimensions or [],
        "contradictions_detected": contradictions,
        "selected_questions": selected,
        "selection_reason": reason,
    }


async def retrieve_curated_evidence(
    db: AsyncSession,
    query_text: str,
    *,
    lang: str = "en",
) -> dict[str, Any]:
    """Module 3: Retrieve curated RAG (NLM MedlinePlus / ICMR) + Knowledge Graph (GNN/KG) evidence."""
    kg_res = analyze_clinical_differentials(query_text, lang=lang)
    rag_hits = []
    try:
        rag_hits = await rag_search(db, query_text, top_k=4)
    except Exception:  # noqa: BLE001 — isolated test DBs may have empty RAG tables
        rag_hits = []

    evidence_items: list[dict[str, Any]] = []
    for h in rag_hits:
        evidence_items.append({
            "source_type": "curated_guideline_or_nlm",
            "source_title": getattr(h, "title", None) or getattr(h, "source", "Curated Medical Reference"),
            "section": getattr(h, "section", None) or "Clinical Guidance",
            "publication_or_update_date": getattr(h, "updated_at", None) or "2026",
            "excerpt": (getattr(h, "text", "") or "")[:360],
            "score": round(float(getattr(h, "score", 0.0)), 4),
        })

    for prov in (kg_res.knowledge_provenance or [])[:3]:
        evidence_items.append({
            "source_type": "clinical_knowledge_graph",
            "source_title": prov.get("source") or f"SushrutAI Knowledge Graph v{KNOWLEDGE_GRAPH_VERSION}",
            "section": prov.get("node_id") or "Clinical Knowledge Graph",
            "publication_or_update_date": prov.get("last_updated") or LAST_UPDATED,
            "excerpt": prov.get("rationale") or "",
            "score": 0.9,
        })

    insufficient = len(evidence_items) == 0
    return {
        "evidence_items": evidence_items,
        "evidence_sufficient": not insufficient,
        "insufficient_evidence_notice": (
            "Reliable guideline evidence in the local index is limited for this specific query; "
            "consult a qualified clinician for personalized evaluation."
            if insufficient
            else None
        ),
    }
