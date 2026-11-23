"""Second safety layer: Llama-Guard-3-1B (local, via Ollama) on the user's message.

Runs after the deterministic red-flag rules. Llama Guard 3 hazard taxonomy (MLCommons):
S1 violent crimes, S2 non-violent crimes, S3 sex crimes, S4 child exploitation, S5 defamation,
S6 specialized advice, S7 privacy, S8 IP, S9 indiscriminate weapons, S10 hate, S11 self-harm,
S12 sexual content, S13 elections, S14 code interpreter abuse.

S6 ("specialized advice") is deliberately ignored: giving careful health information is this product's purpose,
and Llama Guard flags ordinary symptom questions as S6. Fail-open (log) if the model is unavailable — the
deterministic red-flag rules remain the primary safety net.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.config import get_settings
from app.llm.providers import ProviderUnavailable, ollama
from app.safety.red_flags import TELE_MANAS

log = logging.getLogger("sushrut.guard")

IGNORED = {"S6", "S5", "S8", "S13", "S14"}
MESSAGES = {
    "S11": {"en": "You don't have to go through this alone. Please call Tele-MANAS at 14416 (free, 24×7) "
                  "or 112 if you are in immediate danger.",
            "hi": "आप अकेले नहीं हैं। कृपया टेली-मानस 14416 (मुफ़्त, 24×7) पर कॉल करें, या तत्काल खतरे में 112 पर।"},
    "S2": {"en": "I can't help with getting prescription medicines without a prescription. Please see a registered "
                 "doctor — many government hospitals and eSanjeevani (esanjeevani.mohfw.gov.in) offer free consultations.",
           "hi": "मैं बिना पर्चे के दवा पाने में मदद नहीं कर सकता। कृपया पंजीकृत डॉक्टर से मिलें — ई-संजीवनी पर "
                 "मुफ़्त परामर्श उपलब्ध है।"},
    "default": {"en": "I can't help with that request. If you or someone else is in danger, call 112.",
                "hi": "मैं इस अनुरोध में मदद नहीं कर सकता। अगर कोई खतरे में है, तो 112 पर कॉल करें।"},
}


@dataclass
class GuardResult:
    unsafe: bool
    categories: list[str]
    message: str | None = None
    call_numbers: list[str] | None = None
    available: bool = True

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def parse_guard(text: str) -> tuple[bool, list[str]]:
    lines = [x.strip() for x in (text or "").strip().splitlines() if x.strip()]
    if not lines or lines[0].lower() != "unsafe":
        return False, []
    cats = [c.strip().upper() for c in (lines[1] if len(lines) > 1 else "").split(",") if c.strip()]
    return True, cats


# Asking where/how to get a medicine *without* a prescription, or to forge one: refused by rule, whatever the
# guard model says (Llama Guard let "Which pharmacy sells MTP kit without asking for a prescription?" through, and
# the smaller "Faster" model then answered instead of refusing). Asking *whether* a medicine needs one is allowed.
_NO_RX = re.compile(
    r"(?i)((where|which (pharmacy|chemist|shop|store|medical|website|site|app)|how (can|do|to) (i |we )?(get|buy|order)|"
    r"(get|buy|order|sells?|available).{0,40}online|kaha(n|an)?( se)?|कहाँ|कहां).{0,80}"
    r"(without|w/o|no) (a |any |asking for (a )?|needing (a )?)?(prescription|doctor'?s? (note|slip)|rx\b)"
    r"|(without|w/o) (a |any |asking for (a )?)?prescription.{0,60}(where|which (pharmacy|chemist|shop|store)|kaha|कहाँ|कहां)"
    r"|(forge|fake|make up|create)( a)? (doctor'?s? )?prescription"
    r"|(bina|बिना) (parch[ae]|parchi|prescription|पर्च[ेा]|पर्ची|प्रिस्क्रिप्शन).{0,60}(kaha|कहाँ|कहां|kharid|खरीद|mil(e|ega)|मिल))")


def rule_check(text: str, lang: str = "en") -> GuardResult | None:
    if _NO_RX.search(text or ""):
        return GuardResult(True, ["S2"], MESSAGES["S2"].get(lang, MESSAGES["S2"]["en"]), [])
    return None


async def check_user_message(text: str, lang: str = "en") -> GuardResult:
    if (hit := rule_check(text, lang)) is not None:
        return hit
    s = get_settings()
    try:
        res = await ollama().chat(s.guard_model, [{"role": "user", "content": text[:2000]}], temperature=0.0,
                                  num_ctx=2048, num_predict=10)
    except ProviderUnavailable as e:
        log.warning("Llama Guard unavailable (%s) — continuing with rule-based safety only", e)
        return GuardResult(False, [], available=False)
    unsafe, cats = parse_guard(res.text)
    relevant = [c for c in cats if c not in IGNORED]
    if not unsafe or not relevant:
        return GuardResult(False, cats)
    key = "S11" if "S11" in relevant else "S2" if relevant == ["S2"] else "default"
    msg = MESSAGES[key].get(lang, MESSAGES[key]["en"])
    numbers = [TELE_MANAS, "112"] if key == "S11" else ["112"] if key == "default" else []
    return GuardResult(True, relevant, msg, numbers)
