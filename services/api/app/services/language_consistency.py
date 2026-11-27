"""LanguageConsistencyService: Session Language Lock across text, voice, and screen outputs."""
from __future__ import annotations

import threading

from app.langs import LANG_CODES
from app.speech.normalize import (
    BCP47_MAP,
    ConversationLanguageDecision,
    detect_explicit_language_switch,
    normalize_banglish_hinglish,
    resolve_conversation_language,
)

# In-memory fallback lock for guest/no-storage sessions so language lock never resets mid-session.
_SESSION_LANG_LOCK = threading.Lock()
_SESSION_LANGS: dict[str, str] = {}

_CONFIRM_LANG_MSG = {
    "en": "I want to make sure I speak in your preferred language. Would you like me to continue in English, Bengali, or Hindi?",
    "bn": "আমি আপনার পছন্দের ভাষায় কথা বলতে চাই। আমি কি বাংলায়, ইংরেজিতে, নাকি হিন্দিতে কথা বলব?",
    "hi": "मैं आपकी पसंदीदा भाषा में बात करना चाहता हूँ। क्या मैं हिंदी, बंगाली या अंग्रेज़ी में जारी रखूँ?",
}


class LanguageConsistencyService:
    """Enforces Session Language Lock and prevents spurious language switches on English medical terms."""

    def get_locked(self, session_id: str | None) -> str | None:
        if not session_id:
            return None
        with _SESSION_LANG_LOCK:
            return _SESSION_LANGS.get(session_id)

    def set_locked(self, session_id: str | None, lang: str | None) -> None:
        if not session_id or not lang or lang in ("auto", "app"):
            return
        clean = lang.split("-")[0].lower()
        if clean in LANG_CODES:
            with _SESSION_LANG_LOCK:
                _SESSION_LANGS[session_id] = clean

    def clear(self, session_id: str | None) -> None:
        if not session_id:
            return
        with _SESSION_LANG_LOCK:
            _SESSION_LANGS.pop(session_id, None)

    def resolve(
        self,
        text: str,
        *,
        session_id: str | None = None,
        db_locked_lang: str | None = None,
        ui_lang: str = "en",
        explicit_preferred_lang: str | None = None,
        asr_confidence: float | None = None,
    ) -> ConversationLanguageDecision:
        mem_locked = self.get_locked(session_id)
        locked = db_locked_lang or mem_locked
        # Only honor explicit_preferred_lang if explicitly passed as a switch or on turn 1
        decision = resolve_conversation_language(
            text,
            locked_lang=locked,
            ui_lang=ui_lang,
            explicit_preferred_lang=explicit_preferred_lang,
            asr_confidence=asr_confidence,
        )
        # Never allow an ambiguous/English medical term inside a Bengali/Hindi turn to switch away from locked lang
        explicit_switch = detect_explicit_language_switch(text)
        if locked and not explicit_switch and not (
            explicit_preferred_lang and explicit_preferred_lang not in ("auto", "app", "", locked)
        ):
            decision.active_language = locked
            decision.bcp47 = BCP47_MAP.get(locked, locked)
            decision.switched = False
            decision.switch_reason = "session_language_locked"
            decision.resolution.preferred_response_language = locked

        self.set_locked(session_id, decision.active_language)
        return decision

    def normalize_for_clinical(self, text: str, decision: ConversationLanguageDecision) -> str:
        res = decision.resolution
        if res.banglish_detected or res.hinglish_detected:
            return normalize_banglish_hinglish(text)
        return text

    def clarification_prompt(self, lang: str) -> str:
        return _CONFIRM_LANG_MSG.get(lang, _CONFIRM_LANG_MSG["en"])
