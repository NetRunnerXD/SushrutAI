"""Two-stage multilingual transcript processing and critical clinical integrity guard.

Maintains three independent representations:
1. raw_transcript (verbatim ASR output)
2. corrected_transcript (lightweight refinement for punctuation/typos, preserving code-switching)
3. clinical_interpretation (handled downstream via ClinicalExtractionPayload)

CRITICAL SAFETY RULE:
Never silently alter clinically meaningful content (negation, symptom presence, drug names,
dosages, numbers, dates, duration, left/right, severity, allergies, past conditions).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Transcript, TranscriptVersion
from app.security.auth import CurrentUser
from app.speech.normalize import TranscriptAnalysis, analyze_transcript

log = logging.getLogger("sushrut.speech.refine")

_NEG_TOKENS = re.compile(
    r"(?i)\b(?:no|not|never|none|neither|nor|without|don'?t|didn'?t|haven'?t|can'?t|cannot|absent|denied|denies)\b|"
    r"(?:নাই|নেই|না|নয়|কখনো\s+না|পারছি\s+না|पारि|नहीं|कोई\s+नहीं|कभी\s+नहीं)"
)
_NUM_TOKENS = re.compile(r"\d+(?:\.\d+)?|পাঁচ|চার|তিন|দুই|এক|ছয়|সাত|আট|নয়|দশ|five|four|three|two|one|six|seven|eight|nine|ten", re.I)
_LAT_TOKENS = re.compile(r"(?i)\b(?:left|right|bilateral|both\s+sides)\b|বাম|বাঁ|ডান|बाएँ|बायां|दाएँ|दायां")
_SEV_TOKENS = re.compile(r"(?i)\b(?:mild|moderate|severe|extreme|unbearable|worst)\b|হালকা|তীব্র|ভীষণ|খুব\s+বেশি|হাল্কা|तेज़|गंभीर|हल्का")
_MED_HINT = re.compile(
    r"(?i)\b(?:paracetamol|dolo|crocin|metformin|insulin|atorvastatin|amlodipine|telmisartan|aspirin|"
    r"azithromycin|amoxicillin|pantoprazole|ors|mg|ml|mcg|iu)\b"
)

# Normalized safe Bengali/Hindi digit equivalents so '৫' -> '5' does NOT fail the number guard
_DIGIT_TRANS = str.maketrans("০১২৩৪৫৬৭৮৯०१२३४५६७८९", "01234567890123456789")


def _extract_invariants(text: str) -> dict[str, list[str]]:
    norm = (text or "").translate(_DIGIT_TRANS).lower()
    return {
        "neg": sorted(m.group(0).strip() for m in _NEG_TOKENS.finditer(norm)),
        "num": sorted(m.group(0).strip() for m in _NUM_TOKENS.finditer(norm)),
        "lat": sorted(m.group(0).strip() for m in _LAT_TOKENS.finditer(norm)),
        "sev": sorted(m.group(0).strip() for m in _SEV_TOKENS.finditer(norm)),
        "med": sorted(m.group(0).strip() for m in _MED_HINT.finditer(norm)),
    }


def preserves_clinical_invariants(raw: str, candidate: str) -> tuple[bool, list[str]]:
    """Verify candidate refinement did not silently mutate negations, numbers, lateralities, severity, or drugs."""
    r_inv = _extract_invariants(raw)
    c_inv = _extract_invariants(candidate)
    changed: list[str] = []
    for k in ("neg", "num", "lat", "sev", "med"):
        if r_inv[k] != c_inv[k]:
            changed.append(k)
    return len(changed) == 0, changed


@dataclass
class RefinementResult:
    raw_transcript: str
    corrected_transcript: str
    analysis: TranscriptAnalysis
    safety_preserved: bool = True
    rejected_categories: list[str] = field(default_factory=list)
    needs_clarification: bool = False
    clarification_prompt: str | None = None
    refinement_model: str = "deterministic-normalizer"


_REFINE_SYSTEM = """You lightly clean punctuation, spelling, and obvious ASR slips in multilingual (Bengali, English, Banglish) patient transcripts.
STRICT RULES:
1. Preserve code-switching and the speaker's original languages (do NOT translate Bengali into English or vice versa).
2. NEVER add, delete, or change any negation (no, not, never, নেই, না), number, digit, medication name, dosage, duration, date, left/right side, severity, allergy, or symptom presence.
3. Only fix obvious punctuation/spacing or broken ASR spellings without inventing words.
Return JSON: {"corrected": "<string>", "uncertain": <bool>}."""


async def refine_transcript(
    raw_text: str,
    *,
    lang_hint: str = "en",
    context_hint: str = "",
    audio_conf: float | None = None,
    use_llm: bool = False,
) -> RefinementResult:
    """Two-stage recognition refinement preserving raw_transcript and guarding clinical invariants."""
    raw = raw_text or ""
    analysis = analyze_transcript(raw, lang_hint=lang_hint, audio_conf=audio_conf)
    base_clean = analysis.normalized_transcript or raw.strip()

    # Check Stage 1 itself never alters clinical invariants relative to raw
    ok_base, diff_base = preserves_clinical_invariants(raw, base_clean)
    if not ok_base:
        # Fallback to raw with simple digit/whitespace cleanup if normalization accidentally touched an invariant
        safe_clean = " ".join(raw.translate(_DIGIT_TRANS).split())
        analysis.normalized_transcript = safe_clean
        return RefinementResult(
            raw_transcript=raw,
            corrected_transcript=safe_clean,
            analysis=analysis,
            safety_preserved=False,
            rejected_categories=diff_base,
            needs_clarification=True,
            clarification_prompt=analysis.clarification_prompt or _clarify_for(analysis.primary_language),
            refinement_model="invariant-fallback",
        )

    if not use_llm or not base_clean:
        return RefinementResult(
            raw_transcript=raw,
            corrected_transcript=base_clean,
            analysis=analysis,
            safety_preserved=True,
            needs_clarification=analysis.low_confidence,
            clarification_prompt=analysis.clarification_prompt,
            refinement_model="deterministic-normalizer",
        )

    from app.config import get_settings
    from app.llm.providers import ProviderUnavailable, ollama

    s = get_settings()
    model = s.instant_llm_model or s.fast_llm_model
    if not model:
        return RefinementResult(
            raw_transcript=raw,
            corrected_transcript=base_clean,
            analysis=analysis,
            needs_clarification=analysis.low_confidence,
            clarification_prompt=analysis.clarification_prompt,
        )
    try:
        res = await ollama().chat(
            model,
            [
                {"role": "system", "content": _REFINE_SYSTEM},
                {"role": "user", "content": f"Context: {context_hint[:200]}\nRaw ASR: {base_clean}"},
            ],
            schema={
                "type": "object",
                "properties": {"corrected": {"type": "string"}, "uncertain": {"type": "boolean"}},
                "required": ["corrected", "uncertain"],
            },
            temperature=0.0,
            num_ctx=1024,
            num_predict=256,
        )
        data = res.json()
        cand = str(data.get("corrected") or "").strip()
        uncertain = bool(data.get("uncertain"))
        ok_llm, bad_cats = preserves_clinical_invariants(raw, cand) if cand else (False, ["empty"])
        if not ok_llm:
            log.warning("Rejected Stage-2 LLM transcript edit changing invariants %s", bad_cats)
            return RefinementResult(
                raw_transcript=raw,
                corrected_transcript=base_clean,
                analysis=analysis,
                safety_preserved=False,
                rejected_categories=bad_cats,
                needs_clarification=True,
                clarification_prompt=_clarify_for(analysis.primary_language),
                refinement_model=f"guarded-{model}",
            )
        analysis.normalized_transcript = cand
        return RefinementResult(
            raw_transcript=raw,
            corrected_transcript=cand,
            analysis=analysis,
            safety_preserved=True,
            needs_clarification=uncertain or analysis.low_confidence,
            clarification_prompt=_clarify_for(analysis.primary_language) if (uncertain or analysis.low_confidence) else None,
            refinement_model=model,
        )
    except (ProviderUnavailable, ValueError, TypeError):
        return RefinementResult(
            raw_transcript=raw,
            corrected_transcript=base_clean,
            analysis=analysis,
            needs_clarification=analysis.low_confidence,
            clarification_prompt=analysis.clarification_prompt,
        )


def _clarify_for(lang: str) -> str:
    if lang == "bn":
        return "আমি আপনার কথাটি পুরো নিশ্চিতভাবে ধরতে পারিনি — অনুগ্রহ করে একটু ধীরে আবার বলবেন?"
    if lang == "hi":
        return "मैं आपकी बात पूरी तरह साफ़ नहीं समझ पाया — कृपया एक बार फिर से बोलेंगे?"
    return "I want to make sure I heard the exact details correctly — could you repeat or clarify that briefly?"


async def persist_transcript(
    db: AsyncSession,
    cu: CurrentUser,
    *,
    encounter_id: str,
    turn_id: str | None,
    speaker: str = "patient",
    result: RefinementResult,
    is_partial: bool = False,
) -> Transcript:
    """Persist Transcript and immutable TranscriptVersion history for raw and refined stages."""
    t_row = Transcript(
        user_id=cu.id,
        encounter_id=encounter_id,
        turn_id=turn_id,
        speaker=speaker,
        raw_text_enc=cu.cipher.enc(result.raw_transcript),
        corrected_text_enc=cu.cipher.enc(result.corrected_transcript),
        primary_language=result.analysis.primary_language[:16],
        detected_languages=result.analysis.detected_languages,
        code_switched=result.analysis.code_switched,
        confidence=result.analysis.confidence,
        is_partial=is_partial,
    )
    db.add(t_row)
    await db.flush()

    v1 = TranscriptVersion(
        user_id=cu.id,
        transcript_id=t_row.id,
        version_no=1,
        stage="raw_asr",
        text_enc=cu.cipher.enc(result.raw_transcript),
        model_or_actor="asr",
        reason="Verbatim speech recognition output",
        safety_preserved=True,
    )
    db.add(v1)
    if result.corrected_transcript != result.raw_transcript:
        v2 = TranscriptVersion(
            user_id=cu.id,
            transcript_id=t_row.id,
            version_no=2,
            stage="contextual_refinement",
            text_enc=cu.cipher.enc(result.corrected_transcript),
            model_or_actor=result.refinement_model[:64],
            reason="Stage 2 punctuation/code-switch refinement"
            + (f" (rejected unsafe edits: {','.join(result.rejected_categories)})" if not result.safety_preserved else ""),
            safety_preserved=result.safety_preserved,
        )
        db.add(v2)
    await db.flush()
    return t_row
