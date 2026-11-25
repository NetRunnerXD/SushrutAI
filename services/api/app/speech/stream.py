"""Streaming speech recognition session & real-time partial transcription helper.

Processes continuous audio frames (PCM16 little-endian mono 16kHz OR compressed WebM chunks)
from an active consultation session, runs VAD/noise estimation without retaining raw audio on disk,
and emits provisional partial transcripts (<500ms after ASR update) and final refined transcripts.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

import numpy as np

from app.speech import models as M
from app.speech.normalize import SpeechSegment, analyze_transcript, segment_speech_audio

log = logging.getLogger("sushrut.speech.stream")


def pcm16_bytes_to_float(data: bytes) -> np.ndarray:
    """Convert raw signed 16-bit PCM mono bytes to normalized float32 [-1, 1]."""
    if not data or len(data) < 2:
        return np.array([], dtype=np.float32)
    even = len(data) - (len(data) % 2)
    arr = np.frombuffer(data[:even], dtype="<i2").astype(np.float32) / 32768.0
    return arr


@dataclass
class StreamingSpeechSession:
    """In-memory rolling audio buffer for a single live utterance (zero disk persistence)."""

    ui_lang: str = "en"
    lang_mode: str = "auto"  # "auto" or explicit lang code
    sample_rate: int = 16000
    pcm_chunks: list[np.ndarray] = field(default_factory=list)
    compressed_chunks: list[bytes] = field(default_factory=list)
    last_partial_text: str = ""
    last_partial_at: float = 0.0
    detected_lang: str | None = None
    detected_conf: float | None = None

    def append_pcm(self, chunk: bytes) -> None:
        arr = pcm16_bytes_to_float(chunk)
        if len(arr) > 0:
            self.pcm_chunks.append(arr)

    def append_compressed(self, chunk: bytes) -> None:
        if chunk:
            self.compressed_chunks.append(chunk)

    def clear_utterance(self) -> None:
        self.pcm_chunks.clear()
        self.compressed_chunks.clear()
        self.last_partial_text = ""
        self.detected_lang = None
        self.detected_conf = None

    def _current_audio(self) -> np.ndarray:
        if self.pcm_chunks:
            return np.concatenate(self.pcm_chunks)
        if self.compressed_chunks:
            blob = b"".join(self.compressed_chunks)
            try:
                return M.decode_audio(blob, sr=self.sample_rate)
            except Exception as e:  # noqa: BLE001
                log.debug("compressed partial decode failed: %s", e)
        return np.array([], dtype=np.float32)

    async def transcribe(self, *, is_final: bool = False) -> dict | None:
        """Transcribe current utterance buffer, returning partial or final transcript metadata."""
        t0 = time.perf_counter()
        audio = await asyncio.to_thread(self._current_audio)
        if len(audio) < 1600:  # < 0.1 s
            return None

        # Energy VAD check: compute speech spans
        spans = segment_speech_audio(audio, sr=self.sample_rate)
        voiced_s = sum(e - s for s, e in spans)
        if voiced_s < 0.15 and not is_final:
            return None

        target_lang = self.ui_lang if self.lang_mode == "auto" else self.lang_mode
        conf: float | None = None
        if self.lang_mode == "auto" and len(audio) >= self.sample_rate:
            try:
                allowed = [self.ui_lang, "en"] if self.ui_lang == "ur" else [self.ui_lang, "en", "hi"]
                if "bn" in allowed or self.ui_lang == "bn":
                    allowed = list(dict.fromkeys(["bn", "en", *allowed]))
                target_lang, conf = await asyncio.to_thread(M.detect_spoken_language, audio, allowed, self.ui_lang)
                self.detected_lang, self.detected_conf = target_lang, conf
            except Exception:  # noqa: BLE001
                target_lang = self.detected_lang or self.ui_lang

        # Run local ASR (IndicConformer fast CTC on Indic, faster-whisper for English/mixed)
        raw_text = ""
        engine = "none"
        segs: list[dict] = []
        if target_lang in M.LANGS and target_lang != "en" and M.available("onnxruntime") and M.available("transformers"):
            try:
                res = await asyncio.to_thread(M.stt_indic_detailed, audio, target_lang)
                raw_text = res["text"]
                segs = res.get("segments", [])
                engine = "IndicConformer-600M (stream)"
            except Exception as e:  # noqa: BLE001
                log.warning("Streaming IndicConformer failed: %s", e)

        if not raw_text and M.available("faster_whisper"):
            try:
                res = await asyncio.to_thread(M.stt_whisper_detailed, audio, None if self.lang_mode == "auto" else target_lang)
                raw_text = res["text"]
                target_lang = M.LANG_ALIASES.get(res["lang"], res["lang"])
                segs = res.get("segments", [])
                if res.get("confidence") is not None:
                    conf = res["confidence"]
                engine = "faster-whisper (stream)"
            except Exception as e:  # noqa: BLE001
                log.warning("Streaming Whisper failed: %s", e)

        if not raw_text.strip():
            return None

        seg_objs = [
            SpeechSegment(
                start=s.get("start", 0.0),
                end=s.get("end", 0.0),
                text=s.get("text", ""),
                confidence=s.get("confidence"),
                language=s.get("language") or target_lang,
            )
            for s in segs
        ]
        analysis = analyze_transcript(
            raw_text,
            lang_hint=target_lang,
            audio_conf=conf,
            segments=seg_objs,
            audio=audio,
        )
        self.last_partial_text = analysis.normalized_transcript or analysis.raw_transcript
        self.last_partial_at = time.time()
        elapsed_ms = round((time.perf_counter() - t0) * 1000)

        out = {
            "type": "final" if is_final else "partial",
            "is_partial": not is_final,
            "raw_transcript": analysis.raw_transcript,
            "corrected_transcript": analysis.normalized_transcript,
            "text": analysis.normalized_transcript or analysis.raw_transcript,
            "lang": analysis.primary_language,
            "detected_languages": analysis.detected_languages,
            "code_switched": analysis.code_switched,
            "banglish_influenced": analysis.banglish_influenced,
            "confidence": analysis.confidence,
            "low_confidence": analysis.low_confidence,
            "clarification_prompt": analysis.clarification_prompt,
            "segments": [s.as_dict() for s in analysis.segments],
            "engine": engine,
            "latency_ms": elapsed_ms,
        }
        if is_final:
            self.clear_utterance()
        return out
