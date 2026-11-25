"""The languages SushrutAI speaks: one list for the API, speech, translation and the chat."""
from typing import Literal

LangCode = Literal["en", "hi", "bn", "ta", "te", "mr", "gu", "kn", "ml", "or", "pa", "as", "ur"]
LANG_CODES: tuple[str, ...] = LangCode.__args__  # type: ignore[attr-defined]
INDIC: frozenset[str] = frozenset(LANG_CODES) - {"en"}
RTL: frozenset[str] = frozenset({"ur"})
