"""SpeechRecognitionService: ASR analysis, uncertainty clarification & patient-friendly filler cleanup."""
from __future__ import annotations

import re
from typing import Any

from app.speech.normalize import TranscriptAnalysis, analyze_transcript, normalize_transcript
from app.speech.refine import refine_transcript

_FILLER_NOISE = re.compile(
    r"(?i)\b(?:actually\s+what\s+happened\s+was|you\s+know|i\s+mean|let\s+me\s+think|"
    r"wait\s+a\s+second|so\s+basically|actually\s+yesterday\s+my\s+neighbour)\b"
)


class SpeechRecognitionService:
    """Normalizes patient speech, preserves clinical meaning/chronology, and flags uncertain terms."""

    def analyze(
        self,
        raw_text: str,
        *,
        lang_hint: str = "en",
        asr_confidence: float | None = None,
        segments: list[dict] | None = None,
    ) -> TranscriptAnalysis:
        return analyze_transcript(
            raw_text,
            lang_hint=lang_hint,
            asr_confidence=asr_confidence,
            segments=segments,
        )

    async def refine(
        self,
        raw_text: str,
        *,
        lang_hint: str = "en",
        use_llm: bool = False,
    ) -> dict[str, Any]:
        res = await refine_transcript(raw_text, lang_hint=lang_hint, use_llm=use_llm)
        cleaned = normalize_transcript(res.get("refined_transcript") or raw_text, lang_hint=lang_hint)
        cleaned = _FILLER_NOISE.sub(" ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return {
            **res,
            "cleaned_clinical_text": cleaned or raw_text.strip(),
        }

    def uncertain_clarification(self, analysis: TranscriptAnalysis, *, lang: str = "en") -> str | None:
        if not analysis.low_confidence:
            return None
        clean = (lang or "en").split("-")[0].lower()
        if clean == "bn":
            return "আমি আপনার কথার কিছু অংশ স্পষ্ট শুনতে পাইনি। অনুগ্রহ করে ওই অংশটি আরেকবার ধীরে বলবেন?"
        if clean == "hi":
            return "मुझे आपकी बात का कुछ हिस्सा साफ सुनाई नहीं दिया। कृपया वह हिस्सा फिर से धीरे बताएं?"
        return analysis.clarification_prompt or "I did not catch part of that clearly. Could you please repeat that specific detail?"
