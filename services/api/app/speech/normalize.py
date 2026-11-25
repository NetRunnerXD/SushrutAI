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
    r"amar|amader|tomar|apnar|matha|ghurche|ghorche|ghure|betha|byatha|jwor|jor|shorir|kharap|"
    r"kashi|bomi|shwash|shas|koshto|buke|pet|durbol|khub|din dhore|din theke|majhe majhe|"
    r"khabar por|darale|uthle|raat|shokal|bikel|achha|bhalo|lagche|lagchhe|hocche|hochhe|"
    r"parchi na|parchina|osudh|oushodh|daktar|chakkar|kamjori"
    r")\b"
)

_HINGLISH_CUES = re.compile(
    r"(?i)\b("
    r"mujhe|mera|meri|bukhar|bukhaar|dard|khansi|ulti|dast|kamzori|thakan|chakar|saans|"
    r"seene|pet|din se|subah|raat|khane ke baad|dawai|goli|tabiyat|nahi|raha|rahi|hain"
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
    hinglish_hits = len(_HINGLISH_CUES.findall(t))

    langs: list[str] = []
    if n_bn >= 2:
        langs.append("bn")
    if n_hi >= 2:
        langs.append("hi")
    if n_lat >= 3:
        if banglish_hits >= 2 and "bn" not in langs:
            langs.append("bn")
        if hinglish_hits >= 2 and "hi" not in langs:
            langs.append("hi")
        if len(latin_words) >= 2 or n_bn == 0 and n_hi == 0:
            if "en" not in langs:
                langs.append("en")

    if not langs:
        langs = [hint or "en"]

    # Determine primary language
    if n_bn > 0 and n_bn >= n_hi and n_bn >= n_lat * 0.4:
        primary = "bn"
    elif n_hi > 0 and n_hi > n_bn and n_hi >= n_lat * 0.4:
        primary = "hi"
    elif banglish_hits >= 2 and len(latin_words) <= banglish_hits * 2:
        primary = "bn"
    elif hint in langs and len(langs) == 1:
        primary = hint
    else:
        primary = langs[0]

    code_switched = len(set(langs)) > 1
    banglish_influenced = banglish_hits >= 1 or (n_bn > 0 and len(latin_words) >= 1)
    return primary, list(dict.fromkeys(langs)), code_switched, banglish_influenced


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
