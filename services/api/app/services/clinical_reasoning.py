"""ClinicalReasoningService: Structured clinical reasoning pipeline across demographics, history & symptoms."""
from __future__ import annotations

from typing import Any

from app.clinical.cdss import evaluate_cdss_context
from app.clinical.context_manager import ConsultationContext
from app.clinical.pipeline_12layer import run_12layer_clinical_pipeline
from app.services.dietary_guidance import DietaryGuidanceService
from app.services.investigation_recommendation import InvestigationRecommendationService
from app.services.patient_profile import PatientSnapshot
from app.services.specialty_recommendation import SpecialtyRecommendationService


class ClinicalReasoningService:
    """Coordinates deterministic extraction, differential framing, routing, tests, and diet without hallucination."""

    def __init__(self) -> None:
        self.specialty_svc = SpecialtyRecommendationService()
        self.investigation_svc = InvestigationRecommendationService()
        self.diet_svc = DietaryGuidanceService()

    def evaluate(
        self,
        ctx: ConsultationContext,
        *,
        patient: PatientSnapshot,
        lang: str = "en",
        session_id: str | None = None,
        red_flag_triggered: bool = False,
        red_flag_kind: str | None = None,
    ) -> dict[str, Any]:
        pregnant = patient.pregnancy_status in ("pregnant", "possibly_pregnant")
        merged = ctx.merged_clinical_text or ctx.chief_complaint

        cdss = evaluate_cdss_context(
            merged,
            lang=lang,
            age_years=patient.age,
            pregnant=pregnant,
            comorbidities=patient.conditions,
            red_flag_triggered=red_flag_triggered,
            red_flag_kind=red_flag_kind,
        )
        rt = cdss["routing"]
        is_emerg = rt.urgency_level == 1
        is_urgent = rt.urgency_level == 2

        specialty = self.specialty_svc.recommend(rt, lang=lang)
        sig = " ".join([merged, *ctx.symptoms, *patient.conditions, *patient.medications])
        inv_bundle = self.investigation_svc.recommend(
            sig,
            urgent=is_urgent,
            emergency=is_emerg,
            pregnant=pregnant,
            lang=lang,
            max_items=4,
        )
        diet = self.diet_svc.recommend(
            sig,
            dept=rt.primary_department,
            lang=lang,
            age_years=patient.age,
            pregnant=pregnant,
            comorbidities=patient.conditions,
            allergies=patient.allergies,
        )
        cdss["investigations"] = inv_bundle["items"]
        cdss["diet_and_lifestyle"] = diet

        panel = run_12layer_clinical_pipeline(
            merged,
            lang=lang,
            patient_id=patient.user_id,
            encounter_id=session_id,
            age_years=patient.age,
            comorbidities=patient.conditions,
            allergies=patient.allergies,
        )
        # Ensure panel carries our enriched investigations & diet cleanly
        panel["investigations"] = inv_bundle["items"]
        panel["diet_lifestyle"] = diet
        panel["routing"] = {
            **panel.get("routing", {}),
            "primary_department": specialty["primary_department"],
            "primary_department_local": specialty["primary_department_local"],
            "specialty": specialty["specialty"],
            "qualification": specialty["qualification"],
            "primary_reason": specialty["reason"],
            "consult_first_guidance": specialty["consult_first_guidance"],
        }

        return {
            "cdss": cdss,
            "specialty": specialty,
            "investigations": inv_bundle,
            "diet": diet,
            "assessment_panel": panel,
            "diagnostic_status": "possible_condition",  # Never confirmed_diagnosis from symptoms alone
        }

    @staticmethod
    def label_conditions(conditions: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        """Explicitly label AI-inferred conditions as possible/suspected, never confirmed diagnosis."""
        out: list[dict[str, Any]] = []
        for c in conditions or []:
            if not isinstance(c, dict):
                continue
            item = dict(c)
            lik = str(item.get("likelihood") or "").lower()
            item.setdefault("clinical_status", "suspected_condition" if lik == "high" else "possible_condition")
            item["confirmed"] = False
            out.append(item)
        return out
