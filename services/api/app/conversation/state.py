"""ConversationOrchestrator & Turn Management Engine.

Implements:
- Explicit 13-state traceable state machine:
  IDLE, LISTENING, TRANSCRIBING, PAUSE_DETECTED, WAITING_FOR_CONTINUATION,
  TURN_FINALIZING, PROCESSING, GENERATING, SPEAKING, BARGE_IN, CLARIFICATION,
  SESSION_PAUSED, SESSION_ENDED
- Configurable silence thresholds and multi-fragment turn merging
- Immediate barge-in cancellation with monotonic generation_id + asyncio.Event tokens
- Spoken-assistant prefix preservation on interruption
- Session pause/resume continuity
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditEvent, Encounter
from app.security.auth import CurrentUser

log = logging.getLogger("sushrut.conversation.orchestrator")


class ConversationState(str, Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    TRANSCRIBING = "TRANSCRIBING"
    PAUSE_DETECTED = "PAUSE_DETECTED"
    WAITING_FOR_CONTINUATION = "WAITING_FOR_CONTINUATION"
    TURN_FINALIZING = "TURN_FINALIZING"
    PROCESSING = "PROCESSING"
    GENERATING = "GENERATING"
    SPEAKING = "SPEAKING"
    BARGE_IN = "BARGE_IN"
    CLARIFICATION = "CLARIFICATION"
    SESSION_PAUSED = "SESSION_PAUSED"
    SESSION_ENDED = "SESSION_ENDED"


ALLOWED_TRANSITIONS: dict[ConversationState, frozenset[ConversationState]] = {
    ConversationState.IDLE: frozenset({
        ConversationState.LISTENING, ConversationState.PROCESSING, ConversationState.SESSION_PAUSED, ConversationState.SESSION_ENDED,
    }),
    ConversationState.LISTENING: frozenset({
        ConversationState.TRANSCRIBING, ConversationState.PAUSE_DETECTED, ConversationState.TURN_FINALIZING,
        ConversationState.PROCESSING, ConversationState.SESSION_PAUSED, ConversationState.SESSION_ENDED, ConversationState.IDLE,
    }),
    ConversationState.TRANSCRIBING: frozenset({
        ConversationState.PAUSE_DETECTED, ConversationState.WAITING_FOR_CONTINUATION, ConversationState.TURN_FINALIZING,
        ConversationState.CLARIFICATION, ConversationState.LISTENING, ConversationState.SESSION_PAUSED,
    }),
    ConversationState.PAUSE_DETECTED: frozenset({
        ConversationState.LISTENING, ConversationState.WAITING_FOR_CONTINUATION, ConversationState.TURN_FINALIZING,
        ConversationState.SESSION_PAUSED,
    }),
    ConversationState.WAITING_FOR_CONTINUATION: frozenset({
        ConversationState.LISTENING, ConversationState.TRANSCRIBING, ConversationState.TURN_FINALIZING,
        ConversationState.SESSION_PAUSED, ConversationState.SESSION_ENDED,
    }),
    ConversationState.TURN_FINALIZING: frozenset({
        ConversationState.PROCESSING, ConversationState.CLARIFICATION, ConversationState.LISTENING,
    }),
    ConversationState.PROCESSING: frozenset({
        ConversationState.GENERATING, ConversationState.SPEAKING, ConversationState.BARGE_IN,
        ConversationState.CLARIFICATION, ConversationState.IDLE,
    }),
    ConversationState.GENERATING: frozenset({
        ConversationState.SPEAKING, ConversationState.BARGE_IN, ConversationState.CLARIFICATION,
        ConversationState.IDLE, ConversationState.SESSION_PAUSED,
    }),
    ConversationState.SPEAKING: frozenset({
        ConversationState.BARGE_IN, ConversationState.LISTENING, ConversationState.CLARIFICATION,
        ConversationState.IDLE, ConversationState.SESSION_PAUSED,
    }),
    ConversationState.BARGE_IN: frozenset({
        ConversationState.LISTENING, ConversationState.TRANSCRIBING, ConversationState.PROCESSING, ConversationState.IDLE,
    }),
    ConversationState.CLARIFICATION: frozenset({
        ConversationState.LISTENING, ConversationState.SPEAKING, ConversationState.PROCESSING,
        ConversationState.SESSION_PAUSED, ConversationState.IDLE,
    }),
    ConversationState.SESSION_PAUSED: frozenset({
        ConversationState.LISTENING, ConversationState.IDLE, ConversationState.PROCESSING, ConversationState.SESSION_ENDED,
    }),
    ConversationState.SESSION_ENDED: frozenset({ConversationState.IDLE, ConversationState.LISTENING}),
}


@dataclass(frozen=True)
class SilenceConfig:
    """Tunable pause and turn-finalization timings (milliseconds)."""
    pause_detect_ms: int = 1000         # 800-1200 ms: short hesitation
    continuation_grace_ms: int = 5000   # 3-8 s: hold open turn for resuming speech
    gentle_ack_ms: int = 12000          # 10-15 s: calm gentle acknowledgement if desired
    session_pause_ms: int = 50000       # 45-60 s: transition to SESSION_PAUSED

    def as_dict(self) -> dict:
        return {
            "pause_detect_ms": self.pause_detect_ms,
            "continuation_grace_ms": self.continuation_grace_ms,
            "gentle_ack_ms": self.gentle_ack_ms,
            "session_pause_ms": self.session_pause_ms,
        }


@dataclass
class StateTransition:
    from_state: ConversationState
    to_state: ConversationState
    reason: str
    generation_id: str
    at: str


@dataclass
class ActiveRuntime:
    encounter_id: str
    user_id: str
    state: ConversationState = ConversationState.IDLE
    generation_seq: int = 0
    generation_id: str = field(default_factory=lambda: f"gen_{uuid.uuid4().hex[:8]}")
    cancelled_ids: set[str] = field(default_factory=set)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    open_fragments: list[str] = field(default_factory=list)
    spoken_prefix: str | None = None
    transitions: list[StateTransition] = field(default_factory=list)

    def bump_generation(self) -> tuple[str, asyncio.Event]:
        if self.generation_id:
            self.cancelled_ids.add(self.generation_id)
            if len(self.cancelled_ids) > 100:
                self.cancelled_ids = set(list(self.cancelled_ids)[-50:])
        self.cancel_event.set()  # immediately abort previous async generation / playback
        self.generation_seq += 1
        self.generation_id = f"gen_{self.generation_seq}_{uuid.uuid4().hex[:6]}"
        self.cancel_event = asyncio.Event()
        return self.generation_id, self.cancel_event

    def merged_text(self) -> str:
        return " ".join(p.strip() for p in self.open_fragments if p and p.strip()).strip()


class ConversationOrchestrator:
    """Central authority governing conversation state, turn merging, and generation cancellation."""

    def __init__(self, silence_config: SilenceConfig | None = None) -> None:
        self.silence = silence_config or SilenceConfig()
        self._runtimes: dict[str, ActiveRuntime] = {}

    def runtime_for(self, encounter_id: str, user_id: str) -> ActiveRuntime:
        rt = self._runtimes.get(encounter_id)
        if rt is None or rt.user_id != user_id:
            rt = ActiveRuntime(encounter_id=encounter_id, user_id=user_id)
            self._runtimes[encounter_id] = rt
        return rt

    async def transition(
        self,
        db: AsyncSession,
        cu: CurrentUser,
        encounter_id: str,
        to_state: ConversationState,
        *,
        reason: str = "",
        strict: bool = False,
    ) -> dict:
        enc = await db.get(Encounter, encounter_id)
        if enc is None or enc.user_id != cu.id:
            raise KeyError("Encounter not found")
        rt = self.runtime_for(encounter_id, cu.id)
        prev = rt.state
        if to_state != prev and to_state not in ALLOWED_TRANSITIONS.get(prev, frozenset()):
            if strict:
                raise ValueError(f"Illegal state transition {prev.value} -> {to_state.value}")
            log.debug("Coerced non-standard transition %s -> %s (%s)", prev.value, to_state.value, reason)

        rt.state = to_state
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        tr = StateTransition(
            from_state=prev,
            to_state=to_state,
            reason=reason[:160],
            generation_id=rt.generation_id,
            at=now.isoformat() + "Z",
        )
        rt.transitions.append(tr)
        if len(rt.transitions) > 50:
            rt.transitions = rt.transitions[-50:]

        enc.conversation_state = to_state.value
        enc.active_generation_id = rt.generation_id
        if to_state == ConversationState.SESSION_ENDED:
            enc.status = "completed"
            enc.ended_at = now
        db.add(AuditEvent(
            user_id=cu.id,
            actor_id=cu.id,
            actor_role="system",
            action=f"STATE:{prev.value}->{to_state.value}",
            entity_type="encounter",
            entity_id=encounter_id,
            detail_enc=cu.cipher.enc(f"reason={reason}; gen={rt.generation_id}"),
            occurred_at=now,
        ))
        await db.commit()
        return {
            "encounter_id": encounter_id,
            "from_state": prev.value,
            "state": to_state.value,
            "generation_id": rt.generation_id,
            "reason": reason,
            "silence_config": self.silence.as_dict(),
        }

    async def append_fragment(
        self,
        db: AsyncSession,
        cu: CurrentUser,
        encounter_id: str,
        fragment_text: str,
    ) -> dict:
        """Merge incremental speech fragments across patient thinking pauses without prematurely finalizing."""
        enc = await db.get(Encounter, encounter_id)
        if enc is None or enc.user_id != cu.id:
            raise KeyError("Encounter not found")
        rt = self.runtime_for(encounter_id, cu.id)
        if not rt.open_fragments and enc.open_fragment_enc:
            prior = cu.cipher.dec(enc.open_fragment_enc)
            if prior:
                rt.open_fragments.append(prior)

        frag = (fragment_text or "").strip()
        if frag:
            # Avoid duplicating exact overlapping tail when streaming updates resend prefix
            merged_prev = rt.merged_text()
            if merged_prev and frag.lower().startswith(merged_prev.lower()):
                rt.open_fragments = [frag]
            elif not merged_prev.lower().endswith(frag.lower()):
                rt.open_fragments.append(frag)

        merged = rt.merged_text()
        enc.open_fragment_enc = cu.cipher.enc(merged) if merged else None
        if rt.state in (ConversationState.IDLE, ConversationState.PAUSE_DETECTED, ConversationState.WAITING_FOR_CONTINUATION, ConversationState.SESSION_PAUSED):
            await self.transition(db, cu, encounter_id, ConversationState.LISTENING, reason="Speech fragment resumed")
        else:
            await db.commit()
        return {
            "encounter_id": encounter_id,
            "fragments": list(rt.open_fragments),
            "merged_text": merged,
            "state": rt.state.value,
            "generation_id": rt.generation_id,
        }

    async def finalize_turn(self, db: AsyncSession, cu: CurrentUser, encounter_id: str, final_text: str = "") -> dict:
        """Finalize the merged open turn and allocate a fresh generation token for response processing."""
        enc = await db.get(Encounter, encounter_id)
        if enc is None or enc.user_id != cu.id:
            raise KeyError("Encounter not found")
        rt = self.runtime_for(encounter_id, cu.id)
        if final_text.strip():
            await self.append_fragment(db, cu, encounter_id, final_text)
        committed = rt.merged_text()
        rt.open_fragments.clear()
        enc.open_fragment_enc = None
        gen_id, _ = rt.bump_generation()
        await self.transition(db, cu, encounter_id, ConversationState.TURN_FINALIZING, reason="End of turn finalized")
        await self.transition(db, cu, encounter_id, ConversationState.PROCESSING, reason="Extracting & reasoning")
        return {
            "encounter_id": encounter_id,
            "committed_text": committed,
            "generation_id": gen_id,
            "state": rt.state.value,
        }

    async def handle_barge_in(
        self,
        db: AsyncSession,
        cu: CurrentUser,
        encounter_id: str,
        *,
        spoken_prefix: str | None = None,
        interruption_text: str = "",
        cancelled_generation_id: str | None = None,
    ) -> dict:
        """Interrupt active generation/playback immediately, record spoken prefix, and bind new generation ID."""
        enc = await db.get(Encounter, encounter_id)
        if enc is None or enc.user_id != cu.id:
            raise KeyError("Encounter not found")
        rt = self.runtime_for(encounter_id, cu.id)
        if cancelled_generation_id:
            rt.cancelled_ids.add(cancelled_generation_id)
        old_gen = cancelled_generation_id or rt.generation_id
        new_gen, _ = rt.bump_generation()
        if spoken_prefix:
            rt.spoken_prefix = spoken_prefix[:1000]
        await self.transition(
            db,
            cu,
            encounter_id,
            ConversationState.BARGE_IN,
            reason=f"User interrupted during {old_gen}",
        )
        if interruption_text.strip():
            rt.open_fragments = [interruption_text.strip()]
            enc.open_fragment_enc = cu.cipher.enc(interruption_text.strip())
        next_state = ConversationState.LISTENING
        await self.transition(db, cu, encounter_id, next_state, reason="Ready for interrupting utterance")
        return {
            "encounter_id": encounter_id,
            "cancelled_generation_id": old_gen,
            "generation_id": new_gen,
            "spoken_prefix_preserved": rt.spoken_prefix,
            "state": next_state.value,
        }

    def bind_generation(self, encounter_id: str, user_id: str, generation_id: str) -> tuple[bool, asyncio.Event]:
        """Register an active client-supplied generation ID unless already cancelled via barge-in."""
        rt = self.runtime_for(encounter_id, user_id)
        if generation_id in rt.cancelled_ids:
            return False, rt.cancel_event
        rt.generation_seq += 1
        rt.generation_id = generation_id
        rt.cancel_event = asyncio.Event()
        rt.state = ConversationState.PROCESSING
        return True, rt.cancel_event

    def is_generation_current(self, encounter_id: str, user_id: str, generation_id: str) -> bool:
        """Prevent stale LLM/TTS results from overwriting a newer or interrupted turn."""
        rt = self.runtime_for(encounter_id, user_id)
        return (
            generation_id not in rt.cancelled_ids
            and rt.generation_id == generation_id
            and not rt.cancel_event.is_set()
        )

    def cancellation_token(self, encounter_id: str, user_id: str) -> tuple[str, asyncio.Event]:
        rt = self.runtime_for(encounter_id, user_id)
        return rt.generation_id, rt.cancel_event

    def snapshot(self, encounter: Encounter, cu: CurrentUser) -> dict:
        rt = self.runtime_for(encounter.id, cu.id)
        open_frag = rt.merged_text() or (cu.cipher.dec(encounter.open_fragment_enc) if encounter.open_fragment_enc else "") or ""
        return {
            "encounter_id": encounter.id,
            "state": encounter.conversation_state or rt.state.value,
            "generation_id": encounter.active_generation_id or rt.generation_id,
            "open_fragment": open_frag,
            "spoken_prefix": rt.spoken_prefix,
            "interview_outcome": encounter.interview_outcome,
            "questions_asked": encounter.questions_asked,
            "silence_config": self.silence.as_dict(),
            "recent_transitions": [
                {"from": t.from_state.value, "to": t.to_state.value, "reason": t.reason, "gen": t.generation_id, "at": t.at}
                for t in rt.transitions[-15:]
            ],
        }


# Singleton runtime coordinator
conversation_orchestrator = ConversationOrchestrator()
