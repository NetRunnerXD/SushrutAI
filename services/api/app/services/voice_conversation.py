"""VoiceConversationService: Patient-friendly inactivity detection, turn completion & interruption state."""
from __future__ import annotations

import threading
from typing import Any

from app.conversation.state import ConversationOrchestrator, conversation_orchestrator
from app.speech.turn_completion import (
    AdaptivePauseThresholds,
    AdaptiveSpeechCompletionEngine,
    SpeechTurnState,
)

# Configurable listening profiles so slow/pausing speakers are never cut off prematurely.
LISTENING_PROFILES: dict[str, dict[str, Any]] = {
    "patient": {
        "pause_detect_ms": 4000,
        "continuation_grace_ms": 25000,
        "gemini_silence_ms": 3500,
        "thresholds": AdaptivePauseThresholds(short_pause_s=3.0, normal_pause_s=7.0, extended_pause_s=15.0, long_thinking_s=25.0),
    },
    "elderly": {
        "pause_detect_ms": 5000,
        "continuation_grace_ms": 30000,
        "gemini_silence_ms": 4500,
        "thresholds": AdaptivePauseThresholds(short_pause_s=4.0, normal_pause_s=9.0, extended_pause_s=18.0, long_thinking_s=30.0),
    },
    "standard": {
        "pause_detect_ms": 2400,
        "continuation_grace_ms": 12000,
        "gemini_silence_ms": 2000,
        "thresholds": AdaptivePauseThresholds(short_pause_s=2.0, normal_pause_s=5.0, extended_pause_s=10.0, long_thinking_s=15.0),
    },
}


class VoiceConversationService:
    """Coordinates live voice session states, patient listening patience, and fragment merging."""

    def __init__(self, orchestrator: ConversationOrchestrator | None = None) -> None:
        self.orchestrator = orchestrator or conversation_orchestrator
        self._lock = threading.Lock()
        self._engines: dict[str, AdaptiveSpeechCompletionEngine] = {}

    def get_listening_profile(self, mode: str = "patient") -> dict[str, Any]:
        prof = LISTENING_PROFILES.get(mode, LISTENING_PROFILES["patient"])
        return {k: v for k, v in prof.items() if k != "thresholds"}

    def engine_for(self, session_id: str, *, lang: str = "en", mode: str = "patient") -> AdaptiveSpeechCompletionEngine:
        with self._lock:
            eng = self._engines.get(session_id)
            if eng is None:
                thresholds = LISTENING_PROFILES.get(mode, LISTENING_PROFILES["patient"])["thresholds"]
                eng = AdaptiveSpeechCompletionEngine(lang=lang, thresholds=thresholds)
                eng.start_listening()
                self._engines[session_id] = eng
            return eng

    def evaluate_fragment(
        self,
        session_id: str,
        fragment: str,
        *,
        lang: str = "en",
        silence_ms: int = 0,
        explicit_finish: bool = False,
        mode: str = "patient",
    ) -> dict[str, Any]:
        eng = self.engine_for(session_id, lang=lang, mode=mode)
        snap = eng.on_speech_detected(fragment, is_partial=False)
        if explicit_finish and eng.state != SpeechTurnState.TURN_COMPLETED:
            snap = eng.finalize_turn(reason="explicit_button")
        elif silence_ms > 0 and eng.state != SpeechTurnState.TURN_COMPLETED:
            snap = eng.on_silence_elapsed(silence_ms / 1000.0)
        return snap

    def reset_session(self, session_id: str) -> None:
        with self._lock:
            self._engines.pop(session_id, None)
        sessions = getattr(self.orchestrator, "_sessions", None)
        if isinstance(sessions, dict):
            for k in [k for k in sessions if k[0] == session_id]:
                sessions.pop(k, None)

    def status_payload(self, session_id: str, user_id: str) -> dict[str, Any]:
        return self.orchestrator.snapshot(session_id, user_id)
