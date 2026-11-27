"""ConsultationStateMachine: Deterministic 6-stage medical consultation workflow."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ConsultationStage(str, Enum):
    INITIAL_GREETING = "INITIAL_GREETING"
    PATIENT_PROFILE = "PATIENT_PROFILE"
    ACTIVE_CONSULTATION = "ACTIVE_CONSULTATION"
    CLINICAL_ASSESSMENT = "CLINICAL_ASSESSMENT"
    FINAL_RECOMMENDATIONS = "FINAL_RECOMMENDATIONS"
    CONTINUOUS_CONSULTATION = "CONTINUOUS_CONSULTATION"


MIN_FOLLOWUPS_REQUIRED = 2


@dataclass
class SessionConsultationState:
    session_id: str
    stage: ConsultationStage = ConsultationStage.INITIAL_GREETING
    complaints: list[str] = field(default_factory=list)
    followup_rounds: int = 0
    followups_asked: int = 0
    followups_answered: int = 0
    asked_questions: list[str] = field(default_factory=list)
    pending_profile_field: str | None = None
    recommendations_unlocked: bool = False
    emergency_escalated: bool = False
    completed_stages: list[str] = field(default_factory=list)

    def advance_to(self, next_stage: ConsultationStage) -> ConsultationStage:
        if self.stage != next_stage and self.stage.value not in self.completed_stages:
            self.completed_stages.append(self.stage.value)
        self.stage = next_stage
        return self.stage

    @property
    def min_followups_met(self) -> bool:
        return self.followups_answered >= MIN_FOLLOWUPS_REQUIRED

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "stage": self.stage.value,
            "complaints": list(self.complaints),
            "followup_rounds": self.followup_rounds,
            "followups_asked": self.followups_asked,
            "followups_answered": self.followups_answered,
            "min_followups_required": MIN_FOLLOWUPS_REQUIRED,
            "recommendations_unlocked": self.recommendations_unlocked,
            "emergency_escalated": self.emergency_escalated,
            "completed_stages": list(self.completed_stages),
        }


class ConsultationStateMachine:
    """Tracks clinical consultation stage transitions per session at the application logic level."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, SessionConsultationState] = {}

    def get_or_create(self, session_id: str) -> SessionConsultationState:
        with self._lock:
            st = self._sessions.get(session_id)
            if st is None:
                st = SessionConsultationState(session_id=session_id)
                self._sessions[session_id] = st
            return st

    def reset(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def record_followup_question(self, session_id: str, question: str) -> None:
        st = self.get_or_create(session_id)
        q = (question or "").strip()
        if q and q not in st.asked_questions:
            st.asked_questions.append(q)
        st.followups_asked += 1
        st.followup_rounds = st.followups_asked

    def record_followup_answer(self, session_id: str) -> None:
        st = self.get_or_create(session_id)
        if st.followups_asked > st.followups_answered:
            st.followups_answered += 1

    def transition_turn(
        self,
        session_id: str,
        *,
        is_identity_only: bool = False,
        has_missing_profile: bool = False,
        emergency: bool = False,
        symptomatic: bool = False,
        followup_asked: bool = False,
        complaints: list[str] | None = None,
        has_investigations: bool = False,
        has_specialist: bool = False,
        has_summary: bool = False,
        recommendation_ready: bool = False,
        new_symptom_after_final: bool = False,
    ) -> dict[str, Any]:
        st = self.get_or_create(session_id)
        for c in (complaints or []):
            if c and c not in st.complaints:
                st.complaints.append(c)

        if emergency:
            st.emergency_escalated = True
            st.recommendations_unlocked = True
            st.advance_to(ConsultationStage.FINAL_RECOMMENDATIONS)
            return st.as_dict()

        if is_identity_only or (has_missing_profile and not symptomatic and not st.recommendations_unlocked):
            st.advance_to(ConsultationStage.PATIENT_PROFILE)
            return st.as_dict()

        if new_symptom_after_final or (st.recommendations_unlocked and symptomatic and followup_asked):
            st.advance_to(ConsultationStage.CONTINUOUS_CONSULTATION)
            return st.as_dict()

        if symptomatic:
            if recommendation_ready or (not followup_asked and (has_summary or has_specialist or has_investigations) and st.recommendations_unlocked):
                if st.stage == ConsultationStage.FINAL_RECOMMENDATIONS and new_symptom_after_final:
                    st.advance_to(ConsultationStage.CONTINUOUS_CONSULTATION)
                else:
                    st.advance_to(ConsultationStage.CLINICAL_ASSESSMENT)
                    st.advance_to(ConsultationStage.FINAL_RECOMMENDATIONS)
                    st.recommendations_unlocked = True
            elif followup_asked:
                st.advance_to(ConsultationStage.ACTIVE_CONSULTATION)
            else:
                st.advance_to(ConsultationStage.CLINICAL_ASSESSMENT)
                st.advance_to(ConsultationStage.FINAL_RECOMMENDATIONS)
                st.recommendations_unlocked = True
        elif st.recommendations_unlocked:
            st.advance_to(ConsultationStage.CONTINUOUS_CONSULTATION)
        elif has_missing_profile and st.stage == ConsultationStage.INITIAL_GREETING:
            st.advance_to(ConsultationStage.PATIENT_PROFILE)

        return st.as_dict()


consultation_state_machine = ConsultationStateMachine()
