"""Transcript normalization, code-switching language detection, VAD segmentation, and ASR confidence checks.

Preserves `raw_transcript` verbatim while producing a cleaned `normalized_transcript` suitable for
clinical extraction and downstream NLP.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import numpy as np

_BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_HI_DIGITS = str.maketrans("०१२३४৫৬७८९", "0123456789")

# Common speech fillers / disfluencies across English, Bengali, Banglish, and Hindi/Hinglish.
# Kept strictly to non-clinical hesitation sounds so medical words are never stripped.
_FILLERS = re.compile(
    r"(?i)(?:^|\s)(?:"
    r"u+h+|u+m+|e+r+|a+h+|h+m+|"
    r"you know|i mean|like uh|"
    r"মানে মানে|মানে|ইয়ে|ইয়ে|আচ্ছা তো|"
    r"toh na|arre|matlab"
    r")(?=$|[\s,।.])"
)

# Immediate word/phrase repetitions from speech disfluency: "I I feel" -> "I feel", "আমার আমার" -> "আমার".
_REPEAT_WORD = re.compile(r"\b(\w{2,})(?:\s+\1\b)+", re.IGNORECASE | re.UNICODE)
_REPEAT_INDIC = re.compile(r"([\u0900-\u0DFF]{2,})(?:\s+\1)+")
_KEEP_REDUPLICATIVES = frozenset({
    "মাঝে", "কখনো", "কখনো", "ধীরে", "আস্তে", "বার", "হালকা", "কাঁপতে", "একটু", "ঘন",
    "कभी", "धीरे", "बार", "थोड़ा", "हल्का",
})


def _dedupe_indic_match(m: re.Match[str]) -> str:
    word = m.group(1)
    if word in _KEEP_REDUPLICATIVES:
        return f"{word} {word}"
    return word

# Common Bengali-English / rural ASR phonetic normalizations (applied ONLY to normalized_transcript, never raw).
_BN_ASR_FIXES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"ভাল রাচ্ছে না"), "ভালো লাগছে না"),
    (re.compile(r"ভালো রাচ্ছে না"), "ভালো লাগছে না"),
    (re.compile(r"মাথা ঘুরচে|মাথা ঘোরচে"), "মাথা ঘুরছে"),
    (re.compile(r"শরীর খারাপ লাগচে"), "শরীর খারাপ লাগছে"),
    (re.compile(r"বুক ব্যথা|বুকে বেথা"), "বুকে ব্যথা"),
    (re.compile(r"সাস কস্ট|শ্বাস কস্ট"), "শ্বাসকষ্ট"),
)

# Banglish / Bengali-influenced English vocabulary markers
_BANGLISH_CUES = re.compile(
    r"(?i)\b("
    r"amar|amader|amake|tomar|apnar|apni|tumi|tui|"
    r"matha|ghurche|ghorche|ghure|betha|byatha|jwor|jor|shorir|kharap|"
    r"kashi|bomi|shwash|shas|shash|nite|niche|koshto|buke|buk|dhorfor|dhorphor|"
    r"pet|pete|gola|chokh|kaan|chulkani|shardi|sordi|proshrab|paykhana|"
    r"durbol|khub|din|dhore|theke|majhe|tin|char|panch|ek|dui|ekta|ektu|"
    r"khabar|por|darale|uthle|raat|ratre|shokal|shokale|bikel|dupur|achha|bhalo|lagche|lagchhe|"
    r"hocche|hochhe|korche|korchhe|kore|hoye|geche|thake|parchi|parchina|na|ar|chhilo|ache|nei|"
    r"osudh|oushodh|daktar|chakkar|kamjori|rokt|pani|jol|ghum|bojhan|bolun|bolo|banglay|bangla"
    r")\b"
)

# Unambiguously Bengali/Banglish grammatical/clinical tokens that never mean Hindi
_BANGLISH_STRONG = re.compile(
    r"(?i)\b("
    r"amar|amake|amader|korche|korchhe|hochhe|hocche|lagche|lagchhe|ghurche|ghorche|"
    r"parchi|parchina|dhorfor|dhorphor|koshto|betha|byatha|dhore|theke|shash|shwash|"
    r"nite|jwor|bomi|shardi|proshrab|paykhana|chhilo|bojhan|bolun|banglay|chulkani"
    r")\b"
)

_HINGLISH_CUES = re.compile(
    r"(?i)\b("
    r"mujhe|mera|meri|mere|bukhar|bukhaar|dard|khansi|ulti|dast|kamzori|thakan|chakar|chakkar|saans|"
    r"seene|pet|din|se|subah|raat|khane|baad|dawai|goli|tabiyat|nahi|raha|rahi|hain|hai|ho|kar|mein|"
    r"bataiye|samjhaiye|hindi"
    r")\b"
)

_HINGLISH_STRONG = re.compile(
    r"(?i)\b("
    r"mujhe|mera|meri|mere|bukhar|bukhaar|dard|khansi|ulti|dast|saans|seene|"
    r"raha|rahi|hain|hai|nahi|bataiye|samjhaiye|mein"
    r")\b"
)

_BN_SCRIPT = re.compile(r"[\u0980-\u09FF]")
_HI_SCRIPT = re.compile(r"[\u0900-\u097F]")
_LATIN_WORD = re.compile(r"\b[A-Za-z]{2,}\b")

LOW_CONFIDENCE_THRESHOLD = 0.45

CLARIFY_PROMPTS = {
    "en": "I could not hear that clearly because of background noise or unclear audio. Could you please repeat it slowly?",
    "bn": "আমি কথাটা স্পষ্ট বুঝতে পারিনি। অনুগ্রহ করে একটু ধীরে আবার বলবেন?",
    "hi": "मैं आपकी बात साफ़ नहीं सुन पाया। कृपया थोड़ा धीरे दोबारा बोलेंगे?",
}


@dataclass
class SpeechSegment:
    start: float
    end: float
    text: str
    confidence: float | None = None
    language: str | None = None

    def as_dict(self) -> dict:
        return {
            "start": round(self.start, 2),
            "end": round(self.end, 2),
            "text": self.text,
            **({"confidence": round(self.confidence, 3)} if self.confidence is not None else {}),
            **({"language": self.language} if self.language else {}),
        }


@dataclass
class TranscriptAnalysis:
    raw_transcript: str
    normalized_transcript: str
    primary_language: str
    detected_languages: list[str] = field(default_factory=list)
    code_switched: bool = False
    banglish_influenced: bool = False
    confidence: float | None = None
    low_confidence: bool = False
    clarification_prompt: str | None = None
    segments: list[SpeechSegment] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "raw_transcript": self.raw_transcript,
            "normalized_transcript": self.normalized_transcript,
            "primary_language": self.primary_language,
            "detected_languages": self.detected_languages,
            "code_switched": self.code_switched,
            "banglish_influenced": self.banglish_influenced,
            "confidence": self.confidence,
            "low_confidence": self.low_confidence,
            "clarification_prompt": self.clarification_prompt,
            "segments": [s.as_dict() for s in self.segments],
        }


def detect_text_languages(text: str, hint: str = "en") -> tuple[str, list[str], bool, bool]:
    """Detect language(s), code-switching, and Bengali-influenced English (Banglish) from text.

    Returns (primary_lang, detected_languages, code_switched, banglish_influenced).
    """
    t = text or ""
    n_bn = len(_BN_SCRIPT.findall(t))
    n_hi = len(_HI_SCRIPT.findall(t))
    latin_words = _LATIN_WORD.findall(t)
    n_lat = sum(len(w) for w in latin_words)
    banglish_hits = len(_BANGLISH_CUES.findall(t))
    banglish_strong = len(_BANGLISH_STRONG.findall(t))
    hinglish_hits = len(_HINGLISH_CUES.findall(t))
    hinglish_strong = len(_HINGLISH_STRONG.findall(t))
    bn_score = banglish_hits + banglish_strong * 2
    hi_score = hinglish_hits + hinglish_strong * 2

    langs: list[str] = []
    if n_bn >= 2:
        langs.append("bn")
    if n_hi >= 2:
        langs.append("hi")
    if n_lat >= 3:
        if (banglish_hits >= 2 or banglish_strong >= 1) and "bn" not in langs:
            langs.append("bn")
        if (hinglish_hits >= 2 or hinglish_strong >= 1) and hi_score > bn_score and "hi" not in langs:
            langs.append("hi")
        if len(latin_words) >= 2 or n_bn == 0 and n_hi == 0:
            if "en" not in langs:
                langs.append("en")

    if not langs:
        langs = [hint or "en"]

    # Determine primary language (Banglish strong cues prevent Hindi misclassification)
    if n_bn > 0 and n_bn >= n_hi and n_bn >= n_lat * 0.4:
        primary = "bn"
    elif n_hi > 0 and n_hi > n_bn and n_hi >= n_lat * 0.4:
        primary = "hi"
    elif (banglish_hits >= 2 or banglish_strong >= 1) and bn_score >= hi_score and (len(latin_words) <= banglish_hits * 4 or banglish_hits >= 3 or banglish_strong >= 1):
        primary = "bn"
    elif (hinglish_hits >= 2 or hinglish_strong >= 1) and hi_score > bn_score and (len(latin_words) <= hinglish_hits * 4 or hinglish_hits >= 3 or hinglish_strong >= 1):
        primary = "hi"
    elif hint in langs and len(langs) == 1:
        primary = hint
    else:
        primary = langs[0]

    code_switched = len(set(langs)) > 1
    banglish_influenced = banglish_hits >= 1 or banglish_strong >= 1 or (n_bn > 0 and len(latin_words) >= 1)
    return primary, list(dict.fromkeys(langs)), code_switched, banglish_influenced


_ROMAN_CLINICAL_GLOSS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b(?:matha\s+(?:\w+\s+){0,3}(?:betha|byatha)|mathab[ey]tha)\b"), "মাথাব্যথা headache"),
    (re.compile(r"(?i)\b(?:buke?\s+(?:\w+\s+){0,2}(?:betha|byatha|chap))\b"), "বুকে ব্যথা chest pain"),
    (re.compile(r"(?i)\b(?:buk(?:e)?\s+(?:dhorfor|dhorphor))\b"), "বুক ধড়ফড় palpitations"),
    (re.compile(r"(?i)\b(?:pete?\s+(?:\w+\s+){0,2}(?:betha|byatha))\b"), "পেটে ব্যথা stomach pain"),
    (re.compile(r"(?i)\b(?:shwash\s*koshto|shas\s*koshto|shash\s*koshto|sh[wa]+sh\s+nite\s+koshto|shwash\s+niche\s+parchi\s+na)\b"), "শ্বাসকষ্ট shortness of breath"),
    (re.compile(r"(?i)\b(?:matha\s+ghurche|matha\s+ghorche)\b"), "মাথা ঘুরছে dizziness"),
    (re.compile(r"(?i)\b(?:jwor|jor)\b"), "জ্বর fever"),
    (re.compile(r"(?i)\b(?:kashi)\b"), "কাশি cough"),
    (re.compile(r"(?i)\b(?:bomi)\b"), "বমি vomiting"),
    (re.compile(r"(?i)\b(?:acidity|gas\s+hocche)\b"), "অ্যাসিডিটি acidity"),
    (re.compile(r"(?i)\b(?:chulkani)\b"), "চুলকানি itching"),
    (re.compile(r"(?i)\b(?:seene\s+mein\s+dard)\b"), "सीने में दर्द chest pain"),
    (re.compile(r"(?i)\b(?:pet\s+(?:mein\s+)?dard)\b"), "पेट दर्द stomach pain"),
    (re.compile(r"(?i)\b(?:sar\s+(?:mein\s+)?dard|sir\s+dard)\b"), "सिरदर्द headache"),
    (re.compile(r"(?i)\b(?:bukhar|bukhaar)\b"), "बुखार fever"),
    (re.compile(r"(?i)\b(?:khansi)\b"), "खांसी cough"),
    (re.compile(r"(?i)\b(?:saans\s+(?:lene\s+mein\s+)?takleef|saans\s+phoolna)\b"), "सांस फूलना shortness of breath"),
)


def detect_script(text: str) -> str:
    """Identify script: Bengali, Devanagari, Latin, Mixed, or Unknown."""
    t = text or ""
    has_bn = bool(_BN_SCRIPT.search(t))
    has_hi = bool(_HI_SCRIPT.search(t))
    has_lat = bool(_LATIN_WORD.search(t))
    count = int(has_bn) + int(has_hi) + int(has_lat)
    if count > 1:
        return "Mixed"
    if has_bn:
        return "Bengali"
    if has_hi:
        return "Devanagari"
    if has_lat:
        return "Latin"
    return "Unknown"


def normalize_banglish_hinglish(text: str) -> str:
    """Append normalized clinical anchors for Banglish/Hinglish while preserving original text."""
    t = (text or "").strip()
    if not t:
        return ""
    additions: list[str] = []
    for pat, gloss in _ROMAN_CLINICAL_GLOSS:
        if pat.search(t) and gloss not in t:
            additions.append(gloss)
    if not additions:
        return t
    return f"{t} ({', '.join(additions)})"


@dataclass
class LanguageResolution:
    detected_language: str
    confidence: float
    script: str
    mixed_language: bool
    banglish_detected: bool
    hinglish_detected: bool
    preferred_response_language: str
    asr_confidence: float | None = None
    needs_language_clarification: bool = False
    clarification_prompt: str | None = None

    def as_dict(self) -> dict:
        return {
            "detected_language": self.detected_language,
            "confidence": self.confidence,
            "script": self.script,
            "mixed_language": self.mixed_language,
            "banglish_detected": self.banglish_detected,
            "hinglish_detected": self.hinglish_detected,
            "preferred_response_language": self.preferred_response_language,
            "asr_confidence": self.asr_confidence,
            "needs_language_clarification": self.needs_language_clarification,
            "clarification_prompt": self.clarification_prompt,
        }


def resolve_response_language(
    text: str,
    *,
    ui_lang: str = "en",
    explicit_preferred_lang: str | None = None,
    asr_confidence: float | None = None,
) -> LanguageResolution:
    """Detect actual patient language, confidence, script, and decide response language.

    Never blindly overrides clear Bengali/Hindi/Banglish/Hinglish with UI-selected English.
    """
    t = (text or "").strip()
    primary, langs, code_switched, banglish = detect_text_languages(t, hint=ui_lang)
    hinglish = len(_HINGLISH_CUES.findall(t)) >= 2
    script = detect_script(t)

    n_bn = len(_BN_SCRIPT.findall(t))
    n_hi = len(_HI_SCRIPT.findall(t))
    n_lat = sum(len(w) for w in _LATIN_WORD.findall(t))
    banglish_hits = len(_BANGLISH_CUES.findall(t))
    hinglish_hits = len(_HINGLISH_CUES.findall(t))

    if n_bn >= 4 or n_hi >= 4:
        conf = 0.96
    elif banglish_hits >= 3 or hinglish_hits >= 3:
        conf = 0.88
    elif banglish_hits >= 2 or hinglish_hits >= 2:
        conf = 0.78
    elif n_lat >= 6:
        conf = 0.85 if not (banglish_hits or hinglish_hits) else 0.70
    elif len(t) >= 2:
        conf = 0.62
    else:
        conf = 0.30

    if asr_confidence is not None:
        conf = round(min(conf, max(0.0, min(1.0, asr_confidence))), 3)
    else:
        conf = round(conf, 3)

    # Respect explicit user response language preference if explicitly given & distinct from default auto
    if explicit_preferred_lang and explicit_preferred_lang not in ("auto", "app", ""):
        resp_lang = explicit_preferred_lang
    elif conf >= 0.65:
        resp_lang = primary
    else:
        resp_lang = ui_lang or primary

    needs_clarify = conf < LOW_CONFIDENCE_THRESHOLD
    clarify_msg = (
        "I am not fully sure which language you prefer (Bengali, Hindi, or English). "
        "Please let me know your preferred language or repeat your question clearly."
        if needs_clarify
        else None
    )

    return LanguageResolution(
        detected_language=primary,
        confidence=conf,
        script=script,
        mixed_language=code_switched or banglish or hinglish,
        banglish_detected=banglish_hits >= 2,
        hinglish_detected=hinglish,
        preferred_response_language=resp_lang,
        asr_confidence=asr_confidence,
        needs_language_clarification=needs_clarify,
        clarification_prompt=clarify_msg,
    )


BCP47_MAP: dict[str, str] = {
    "en": "en-IN",
    "hi": "hi-IN",
    "bn": "bn-IN",
    "ta": "ta-IN",
    "te": "te-IN",
    "mr": "mr-IN",
    "gu": "gu-IN",
    "kn": "kn-IN",
    "ml": "ml-IN",
    "or": "or-IN",
    "pa": "pa-IN",
    "as": "as-IN",
    "ur": "ur-IN",
}

_SWITCH_TO_BN = re.compile(
    r"(?i)(?:"
    r"\b(?:amake\s+)?bangla(?:y|te)?\s+(?:bojhan|bolun|bolo|kotha\s+bolun|uttor\s+din)\b|"
    r"\b(?:speak|explain|reply|answer|talk|switch(?:\s+to)?)\s+(?:in\s+)?bengali\b|"
    r"\bbengali\s+please\b|"
    r"বাংলা(?:য়|য়|তে)?\s*(?:বলুন|বলো|বোঝান|উত্তর\s*দিন|কথা\s*বলুন)"
    r")"
)
_SWITCH_TO_EN = re.compile(
    r"(?i)(?:"
    r"\b(?:now\s+)?(?:speak|explain|reply|answer|talk|switch(?:\s+to)?)\s+(?:in\s+)?english\b|"
    r"\benglish\s+(?:e|te|mein|me)\s+(?:bolun|bolo|bataiye|samjhaiye|bojhan)\b|"
    r"\bin\s+english\s+please\b|"
    r"ইংরেজি(?:তে)?\s*(?:বলুন|বোঝান)|"
    r"अंग्रेजी\s*में\s*(?:बताइए|बोलिए|समझाइए)"
    r")"
)
_SWITCH_TO_HI = re.compile(
    r"(?i)(?:"
    r"\bhindi\s+(?:mein|me)\s+(?:bataiye|batao|bolo|boliye|samjhaiye|samjhao)\b|"
    r"\b(?:speak|explain|reply|answer|talk|switch(?:\s+to)?)\s+(?:in\s+)?hindi\b|"
    r"\bhindi\s+please\b|"
    r"हिन्दी\s*में\s*(?:बताइए|बोलिए|समझाइए)|"
    r"हिंदी\s*में\s*(?:बताइए|बोलिए|समझाइए)|"
    r"হিন্দিতে\s*(?:বলুন|বোঝান)"
    r")"
)


_OTHER_LANG_SWITCHES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ta", re.compile(r"(?i)\b(?:speak|reply|answer|talk|switch(?:\s+to)?)\s+(?:in\s+)?tamil\b|தமிழில்\s*(?:பேசுங்கள்|சொல்லுங்கள்)")),
    ("te", re.compile(r"(?i)\b(?:speak|reply|answer|talk|switch(?:\s+to)?)\s+(?:in\s+)?telugu\b|తెలుగులో\s*(?:చెప్పండి|మాట్లాడండి)")),
    ("mr", re.compile(r"(?i)\b(?:speak|reply|answer|talk|switch(?:\s+to)?)\s+(?:in\s+)?marathi\b|मराठीत\s*(?:बोला|सांगा)")),
    ("gu", re.compile(r"(?i)\b(?:speak|reply|answer|talk|switch(?:\s+to)?)\s+(?:in\s+)?gujarati\b|ગુજરાતીમાં\s*(?:બોલો|જણાવો)")),
    ("kn", re.compile(r"(?i)\b(?:speak|reply|answer|talk|switch(?:\s+to)?)\s+(?:in\s+)?kannada\b|ಕನ್ನಡದಲ್ಲಿ\s*(?:ಮಾತನಾಡಿ|ತಿಳಿಸಿ)")),
    ("ml", re.compile(r"(?i)\b(?:speak|reply|answer|talk|switch(?:\s+to)?)\s+(?:in\s+)?malayalam\b|മലയാളത്തിൽ\s*(?:പറയൂ|സംസാരിക്കൂ)")),
    ("or", re.compile(r"(?i)\b(?:speak|reply|answer|talk|switch(?:\s+to)?)\s+(?:in\s+)?(?:odia|oriya)\b|ଓଡ଼ିଆରେ\s*(?:କୁହନ୍ତୁ|କହନ୍ତୁ)")),
    ("pa", re.compile(r"(?i)\b(?:speak|reply|answer|talk|switch(?:\s+to)?)\s+(?:in\s+)?punjabi\b|ਪੰਜਾਬੀ\s*ਵਿੱਚ\s*(?:ਬੋਲੋ|ਦੱਸੋ)")),
    ("as", re.compile(r"(?i)\b(?:speak|reply|answer|talk|switch(?:\s+to)?)\s+(?:in\s+)?assamese\b|অসমীয়াত\s*(?:কওক|কওঁচোন)")),
    ("ur", re.compile(r"(?i)\b(?:speak|reply|answer|talk|switch(?:\s+to)?)\s+(?:in\s+)?urdu\b|اردو\s*میں\s*(?:بتائیں|بولیں)")),
)


def detect_explicit_language_switch(text: str) -> str | None:
    """Return target language code ONLY when the user explicitly requests a language switch."""
    t = (text or "").strip()
    if not t:
        return None
    if _SWITCH_TO_BN.search(t):
        return "bn"
    if _SWITCH_TO_EN.search(t):
        return "en"
    if _SWITCH_TO_HI.search(t):
        return "hi"
    for code, pat in _OTHER_LANG_SWITCHES:
        if pat.search(t):
            return code
    return None


@dataclass
class ConversationLanguageDecision:
    active_language: str
    bcp47: str
    locked: bool
    switched: bool
    switch_reason: str
    resolution: LanguageResolution

    def as_dict(self) -> dict:
        return {
            **self.resolution.as_dict(),
            "preferred_response_language": self.active_language,
            "active_language": self.active_language,
            "bcp47": self.bcp47,
            "locked": self.locked,
            "switched": self.switched,
            "switch_reason": self.switch_reason,
        }


def resolve_conversation_language(
    text: str,
    *,
    locked_lang: str | None = None,
    ui_lang: str = "en",
    explicit_preferred_lang: str | None = None,
    asr_confidence: float | None = None,
) -> ConversationLanguageDecision:
    """Conversation Language Controller: locks the conversation language after turn 1 and only changes
    it when the patient explicitly requests another language (or passes explicit_preferred_lang)."""
    res = resolve_response_language(
        text,
        ui_lang=ui_lang,
        explicit_preferred_lang=explicit_preferred_lang,
        asr_confidence=asr_confidence,
    )
    explicit_switch = detect_explicit_language_switch(text)
    clean_locked = locked_lang if locked_lang not in (None, "", "auto", "app") else None

    if explicit_preferred_lang and explicit_preferred_lang not in ("auto", "app", ""):
        active = explicit_preferred_lang
        switched = clean_locked is not None and active != clean_locked
        reason = "explicit_preferred_lang"
    elif explicit_switch:
        active = explicit_switch
        switched = clean_locked is not None and active != clean_locked
        reason = "explicit_user_switch"
    elif clean_locked:
        # Language Lock: preserve the active conversation language across short/mixed turns!
        active = clean_locked
        switched = False
        reason = "conversation_locked"
    else:
        active = res.preferred_response_language
        switched = False
        reason = "initial_detection"

    res.preferred_response_language = active
    return ConversationLanguageDecision(
        active_language=active,
        bcp47=BCP47_MAP.get(active, active),
        locked=True,
        switched=switched,
        switch_reason=reason,
        resolution=res,
    )


def normalize_transcript(raw_text: str, lang_hint: str = "en") -> str:
    """Clean fillers, disfluency repetitions, Bengali/Hindi numerals, and obvious ASR slips
    while preserving clinical meaning and keeping `raw_transcript` untouched elsewhere."""
    text = unicodedata.normalize("NFC", (raw_text or "").strip())
    if not text:
        return ""

    # Normalize digits so downstream temporal and vital extractors read '৫ দিন' / '5 days' uniformly
    text = text.translate(_BN_DIGITS).translate(_HI_DIGITS)

    for pat, repl in _BN_ASR_FIXES:
        text = pat.sub(repl, text)

    # Remove hesitation fillers iteratively
    prev = None
    while prev != text:
        prev = text
        text = _FILLERS.sub(" ", text)

    # Collapse stuttering word repetitions ("I I have", "আমার আমার") while keeping grammar reduplicatives ("মাঝে মাঝে")
    text = _REPEAT_WORD.sub(r"\1", text)
    text = _REPEAT_INDIC.sub(_dedupe_indic_match, text)

    # Clean punctuation spacing
    text = re.sub(r"\s+([,.;:!?।])", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def segment_speech_audio(audio: np.ndarray, sr: int = 16000, frame_ms: int = 30,
                         min_silence_ms: int = 300) -> list[tuple[float, float]]:
    """Lightweight energy-based VAD segmentation returning (start_s, end_s) intervals.
    Used to provide segment timestamps for engines like IndicConformer that output flat strings."""
    if audio is None or len(audio) == 0:
        return []
    frame_len = max(1, int(sr * frame_ms / 1000))
    n_frames = len(audio) // frame_len
    if n_frames == 0:
        return [(0.0, round(len(audio) / sr, 2))]

    frames = audio[: n_frames * frame_len].reshape(n_frames, frame_len)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    noise_floor = float(np.percentile(rms, 15))
    thresh = max(0.012, noise_floor * 2.5)
    voiced = rms > thresh

    # Bridge short pauses
    gap_frames = max(1, min_silence_ms // frame_ms)
    smoothed = voiced.copy()
    i = 0
    while i < len(smoothed):
        if not smoothed[i]:
            j = i
            while j < len(smoothed) and not smoothed[j]:
                j += 1
            if i > 0 and j < len(smoothed) and (j - i) <= gap_frames:
                smoothed[i:j] = True
            i = j
        else:
            i += 1

    spans: list[tuple[float, float]] = []
    start_idx = None
    for idx, flag in enumerate(smoothed):
        if flag and start_idx is None:
            start_idx = idx
        elif not flag and start_idx is not None:
            spans.append((round(start_idx * frame_ms / 1000.0, 2), round(idx * frame_ms / 1000.0, 2)))
            start_idx = None
    if start_idx is not None:
        spans.append((round(start_idx * frame_ms / 1000.0, 2), round(len(audio) / sr, 2)))
    if not spans:
        spans = [(0.0, round(len(audio) / sr, 2))]
    return spans


def estimate_audio_confidence(audio: np.ndarray | None, text: str, raw_conf: float | None = None) -> float:
    """Estimate transcription reliability in [0, 1] combining engine probability, SNR, and text sanity."""
    if not (text or "").strip():
        return 0.0
    score = raw_conf if raw_conf is not None else 0.78
    if audio is not None and len(audio) > 0:
        rms = float(np.sqrt(np.mean(audio ** 2)))
        noise = float(np.percentile(np.abs(audio), 20)) + 1e-6
        snr_est = rms / noise
        if rms < 0.004:
            score = min(score, 0.35)
        elif snr_est < 1.3:
            score = min(score, 0.42)
    words = text.strip().split()
    if len(words) == 1 and len(words[0]) <= 2 and score < 0.6:
        score = min(score, 0.38)
    return round(max(0.0, min(1.0, score)), 3)


def analyze_transcript(
    raw_transcript: str,
    *,
    lang_hint: str = "en",
    audio_conf: float | None = None,
    segments: list[SpeechSegment] | None = None,
    audio: np.ndarray | None = None,
) -> TranscriptAnalysis:
    norm = normalize_transcript(raw_transcript, lang_hint=lang_hint)
    primary, langs, code_switched, banglish = detect_text_languages(norm or raw_transcript, hint=lang_hint)
    conf = estimate_audio_confidence(audio, norm, raw_conf=audio_conf)
    low = conf < LOW_CONFIDENCE_THRESHOLD or not norm
    prompt = CLARIFY_PROMPTS.get(primary, CLARIFY_PROMPTS["en"]) if low else None
    return TranscriptAnalysis(
        raw_transcript=raw_transcript,
        normalized_transcript=norm,
        primary_language=primary,
        detected_languages=langs,
        code_switched=code_switched,
        banglish_influenced=banglish,
        confidence=conf,
        low_confidence=low,
        clarification_prompt=prompt,
        segments=segments or [],
    )
