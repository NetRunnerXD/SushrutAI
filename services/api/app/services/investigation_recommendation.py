"""InvestigationRecommendationService: Clinically cautious, symptom-matched test considerations."""
from __future__ import annotations

from typing import Any

from app.clinical.investigations import recommend_for_turn


class InvestigationRecommendationService:
    """Produces clinician-reviewed investigation possibilities with rationale, yield & preparation."""

    def recommend(
        self,
        signal_text: str,
        *,
        duration_days: float = 0.0,
        urgent: bool = False,
        emergency: bool = False,
        pregnant: bool = False,
        lang: str = "en",
        max_items: int = 4,
    ) -> dict[str, Any]:
        clean = (lang or "en").split("-")[0].lower()
        if emergency:
            note = (
                "জরুরি চিকিৎসায় কোনো দেরি করবেন না; হাসপাতালের জরুরি বিভাগ তাৎক্ষণিক প্রয়োজনীয় পরীক্ষা ঠিক করবে।"
                if clean == "bn"
                else ("आपातकालीन देखभाल में देरी न करें; इमरजेंसी डॉक्टर तुरंत आवश्यक जांच तय करेंगे।" if clean == "hi" else "Do not delay emergency treatment for routine tests; emergency clinicians decide immediate tests.")
            )
            return {"items": [], "clinician_note": note}

        raw_items = recommend_for_turn(
            signal_text,
            duration_days=duration_days,
            urgent=urgent,
            emergency=emergency,
            pregnant=pregnant,
            max_items=max_items,
        )
        items: list[dict[str, Any]] = []
        for item in raw_items:
            domain = str(item.get("domain_category") or "blood")
            if domain in ("cardiac",):
                interp = "Cardiologist / Treating Physician"
            elif domain in ("imaging",):
                interp = "Radiologist & Treating Specialist"
            elif domain in ("neurological",):
                interp = "Neurologist"
            elif domain in ("respiratory",):
                interp = "Pulmonologist / Physician"
            else:
                interp = "Treating Physician / Pathologist"
            items.append({
                "test_name": item["investigation_name"],
                "investigation_name": item["investigation_name"],
                "why_relevant": item["why_relevant"],
                "what_it_provides": item.get("what_it_provides") or item.get("what_it_evaluates") or item["why_relevant"],
                "what_it_investigates": ", ".join(item.get("supporting_findings") or []) or item.get("category", "diagnostic evaluation"),
                "clinician_assessment_required": bool(item.get("clinician_decision_required", True)),
                "interpreter_role": interp,
                "preparation": item.get("preparation") or "No special preparation usually needed; confirm with your doctor or lab.",
                "urgency": item.get("urgency", "routine"),
                "urgency_tier": item.get("urgency_tier", "conditional"),
                "category": item.get("category", "diagnostic"),
                "stewardship_level": item.get("stewardship_level", 2),
                "limitations": item.get("limitations", ""),
            })

        note = (
            "এগুলো কেবল সম্ভাব্য পরীক্ষা—শারীরিক পরীক্ষার পর চিকিৎসক প্রয়োজন মনে করলে এগুলো করতে বলতে পারেন।"
            if clean == "bn"
            else ("ये केवल संभावित जांच हैं—शारीरिक जांच के बाद डॉक्टर आवश्यकतानुसार तय करेंगे।" if clean == "hi" else "These are possibilities for a qualified clinician to consider after physical examination, not mandatory orders.")
        )
        return {"items": items, "clinician_note": note}
