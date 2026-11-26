"""Module 2 & 3: Adaptive Speech Completion Engine & Incremental Transcript Aggregation.

Implements the 10-state patient-friendly speech turn machine:
  1. IDLE
  2. LISTENING
  3. SPEECH_DETECTED
  4. TEMPORARY_PAUSE        (0-3 s)
  5. PATIENT_THINKING       (3-7 s)
  6. CONTINUATION_EXPECTED  (7-15 s)
  7. FINALIZATION_PENDING   (15-25 s)
  8. TURN_COMPLETED         (explicit finish or >25 s confirmed)
  9. AI_PROCESSING
 10. AI_RESPONDING

Maintains three separate transcript layers:
  - partial_transcript
  - current_turn_transcript (deduplicated incremental aggregation)
  - finalized_transcript
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class SpeechTurnState(str, Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    SPEECH_DETECTED = "SPEECH_DETECTED"
    TEMPORARY_PAUSE = "TEMPORARY_PAUSE"
    PATIENT_THINKING = "PATIENT_THINKING"
    CONTINUATION_EXPECTED = "CONTINUATION_EXPECTED"
    FINALIZATION_PENDING = "FINALIZATION_PENDING"
    TURN_COMPLETED = "TURN_COMPLETED"
    AI_PROCESSING = "AI_PROCESSING"
    AI_RESPONDING = "AI_RESPONDING"


@dataclass(frozen=True)
class AdaptivePauseThresholds:
    short_pause_s: float = 3.0        # 0-3s: TEMPORARY_PAUSE
    normal_pause_s: float = 7.0       # 3-7s: PATIENT_THINKING
    extended_pause_s: float = 15.0    # 7-15s: CONTINUATION_EXPECTED
    long_thinking_s: float = 25.0     # 15-25s: FINALIZATION_PENDING; >25s gentle check


_EXPLICIT_FINISH_RE = re.compile(
    r"(?i)(?:আমি\s*বলা\s*শেষ\s*করেছি|বলা\s*শেষ|আমার\s*বলা\s*হয়ে\s*গেছে|"
    r"\bi'?m\s+finished\s+speaking\b|\bi\s+am\s+finished\s+speaking\b|\bi'?m\s+done\s+speaking\b|\bthat'?s\s+all\s+for\s+now\b|"
    r"मैंने\s+बोल\s+दिया\s+है|मेरा\s+बोलना\s+हो\s+गया)"
)

GENTLE_TIME_CHECK = {
    "bn": "আপনি কি আরও কিছু বলতে চান, নাকি আমি এখন উত্তর দেব?",
    "en": "Would you like more time to continue speaking, or should I respond now?",
    "hi": "क्या आप कुछ और कहना चाहते हैं, या मैं अभी उत्तर दूँ?",
}


def is_explicit_finish_command(text: str) -> bool:
    return bool(_EXPLICIT_FINISH_RE.search((text or "").strip()))


@dataclass
class AdaptiveSpeechCompletionEngine:
    """State machine preventing premature turn completion on elderly/thinking pauses."""

    lang: str = "bn"
    thresholds: AdaptivePauseThresholds = field(default_factory=AdaptivePauseThresholds)
    state: SpeechTurnState = SpeechTurnState.IDLE
    partial_transcript: str = ""
    turn_segments: list[str] = field(default_factory=list)
    segment_timestamps: list[str] = field(default_factory=list)
    finalized_transcript: str = ""
    gentle_check_prompted: bool = False
    reopened_count: int = 0
    spoken_prefix_before_barge_in: str | None = None

    @property
    def current_turn_transcript(self) -> str:
        return " ".join(s for s in self.turn_segments if s).strip()

    def start_listening(self) -> SpeechTurnState:
        self.state = SpeechTurnState.LISTENING
        self.gentle_check_prompted = False
        return self.state

    def on_speech_detected(self, text: str, *, is_partial: bool = False) -> dict[str, Any]:
        """Ingest partial or incremental finalized speech; cancels pending finalization immediately."""
        clean = " ".join((text or "").split()).strip()
        if not clean:
            return self.snapshot()

        # If AI was responding -> barge-in
        if self.state == SpeechTurnState.AI_RESPONDING:
            self.on_barge_in()

        # Cancel any pending finalization if new speech arrives
        if self.state in (
            SpeechTurnState.IDLE,
            SpeechTurnState.LISTENING,
            SpeechTurnState.TEMPORARY_PAUSE,
            SpeechTurnState.PATIENT_THINKING,
            SpeechTurnState.CONTINUATION_EXPECTED,
            SpeechTurnState.FINALIZATION_PENDING,
        ):
            self.state = SpeechTurnState.SPEECH_DETECTED
            self.gentle_check_prompted = False

        if is_partial:
            self.partial_transcript = clean
            return self.snapshot()

        # Explicit voice finish command?
        if is_explicit_finish_command(clean):
            stripped = _EXPLICIT_FINISH_RE.sub("", clean).strip(" ,.।")
            if stripped:
                self._append_dedup(stripped)
            return self.finalize_turn(reason="explicit_voice_command")

        self._append_dedup(clean)
        self.partial_transcript = ""
        return self.snapshot()

    def _append_dedup(self, clean: str) -> None:
        """Append segment while suppressing exact/trailing duplicates."""
        if not clean:
            return
        if self.turn_segments:
            last = self.turn_segments[-1].lower()
            low = clean.lower()
            if low == last or self.current_turn_transcript.lower().endswith(low):
                return
            if low.startswith(last) and len(low) > len(last):
                self.turn_segments[-1] = clean
                self.segment_timestamps[-1] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                return
        self.turn_segments.append(clean)
        self.segment_timestamps.append(datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))

    def on_silence_elapsed(self, silence_seconds: float) -> dict[str, Any]:
        """Advance pause state based on elapsed silence; never auto-completes during 0-25s pauses."""
        if self.state in (SpeechTurnState.IDLE, SpeechTurnState.TURN_COMPLETED, SpeechTurnState.AI_PROCESSING, SpeechTurnState.AI_RESPONDING):
            return self.snapshot()

        t = self.thresholds
        prompt: str | None = None
        if silence_seconds < t.short_pause_s:
            self.state = SpeechTurnState.TEMPORARY_PAUSE
        elif silence_seconds < t.normal_pause_s:
            self.state = SpeechTurnState.PATIENT_THINKING
        elif silence_seconds < t.extended_pause_s:
            self.state = SpeechTurnState.CONTINUATION_EXPECTED
        elif silence_seconds <= t.long_thinking_s:
            self.state = SpeechTurnState.FINALIZATION_PENDING
        else:
            self.state = SpeechTurnState.FINALIZATION_PENDING
            if not self.gentle_check_prompted:
                self.gentle_check_prompted = True
                prompt = GENTLE_TIME_CHECK.get(self.lang, GENTLE_TIME_CHECK["en"])

        snap = self.snapshot()
        if prompt:
            snap["gentle_check_prompt"] = prompt
        return snap

    def finalize_turn(self, *, reason: str = "explicit_button") -> dict[str, Any]:
        """Explicitly complete the patient's turn and move to TURN_COMPLETED."""
        if self.partial_transcript and not self.current_turn_transcript.lower().endswith(self.partial_transcript.lower()):
            self._append_dedup(self.partial_transcript)
        self.partial_transcript = ""
        self.finalized_transcript = self.current_turn_transcript
        self.state = SpeechTurnState.TURN_COMPLETED
        snap = self.snapshot()
        snap["finalized_reason"] = reason
        return snap

    def begin_ai_processing(self) -> SpeechTurnState:
        self.state = SpeechTurnState.AI_PROCESSING
        return self.state

    def begin_ai_responding(self) -> SpeechTurnState:
        self.state = SpeechTurnState.AI_RESPONDING
        return self.state

    def on_barge_in(self, spoken_so_far: str = "") -> dict[str, Any]:
        """Interrupt AI response cleanly and return to LISTENING without losing turn context."""
        if spoken_so_far:
            self.spoken_prefix_before_barge_in = spoken_so_far[:1000]
        self.state = SpeechTurnState.LISTENING
        self.gentle_check_prompted = False
        return self.snapshot()

    def reopen_with_addition(self, new_speech: str) -> dict[str, Any]:
        """Reopen a completed turn if the patient remembers another symptom afterward."""
        self.reopened_count += 1
        self.state = SpeechTurnState.SPEECH_DETECTED
        self._append_dedup(" ".join((new_speech or "").split()).strip())
        self.finalized_transcript = self.current_turn_transcript
        return self.snapshot()

    def reset_for_next_turn(self) -> None:
        self.turn_segments.clear()
        self.segment_timestamps.clear()
        self.partial_transcript = ""
        self.finalized_transcript = ""
        self.gentle_check_prompted = False
        self.state = SpeechTurnState.LISTENING

    def snapshot(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "partial_transcript": self.partial_transcript,
            "current_turn_transcript": self.current_turn_transcript,
            "finalized_transcript": self.finalized_transcript,
            "segment_count": len(self.turn_segments),
            "reopened_count": self.reopened_count,
            "gentle_check_prompted": self.gentle_check_prompted,
        }
