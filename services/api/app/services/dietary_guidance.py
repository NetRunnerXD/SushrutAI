"""DietaryGuidanceService: Safe, complaint-specific food, hydration & lifestyle guidance."""
from __future__ import annotations

from typing import Any

from app.clinical.diet_lifestyle import recommend_diet_and_lifestyle


class DietaryGuidanceService:
    """Wraps clinical diet & lifestyle logic into a normalized A–E guidance schema."""

    def recommend(
        self,
        signal_text: str,
        *,
        dept: str = "General Medicine",
        lang: str = "en",
        age_years: int | None = None,
        pregnant: bool = False,
        comorbidities: list[str] | None = None,
        allergies: list[str] | None = None,
    ) -> dict[str, Any]:
        raw = recommend_diet_and_lifestyle(
            signal_text,
            dept=dept,
            lang=lang,
            age_years=age_years,
            pregnant=pregnant,
            comorbidities=comorbidities,
            allergies=allergies,
        )
        suitable = list(raw.get("helpful_foods") or [])
        limit = list(raw.get("foods_to_limit") or [])
        hydration = str(raw.get("hydration_guidance") or raw.get("hydration") or "")
        lifestyle = list(raw.get("lifestyle_modifications") or raw.get("lifestyle") or [])
        precautions = [str(x) for x in [raw.get("precaution_note"), raw.get("allergy_note")] if x]
        if allergies:
            allergy_txt = ", ".join(allergies)
            if not any(allergy_txt in p for p in precautions):
                precautions.append(f"Avoid known allergens ({allergy_txt}).")
        return {
            **raw,
            "suitable_foods": suitable,
            "foods_to_limit": limit,
            "hydration_guidance": hydration,
            "lifestyle_recommendations": lifestyle,
            "precautions": precautions,
        }
