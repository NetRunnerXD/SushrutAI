"""Lightweight, strictly non-PII in-memory latency and reliability telemetry."""
from __future__ import annotations

import threading
import time
from collections import deque

_LOCK = threading.Lock()
_SAMPLES: dict[str, deque[float]] = {
    "stt_ms": deque(maxlen=100),
    "tts_ms": deque(maxlen=100),
    "llm_ms": deque(maxlen=100),
}
_COUNTERS: dict[str, int] = {
    "stt_requests": 0,
    "tts_requests": 0,
    "llm_requests": 0,
    "api_errors": 0,
    "barge_in_count": 0,
    "session_recoveries": 0,
    "red_flag_overrides": 0,
}
_STARTED_AT = time.time()


def record_latency(metric: str, ms: float) -> None:
    with _LOCK:
        if metric in _SAMPLES:
            _SAMPLES[metric].append(max(0.0, float(ms)))


def inc(counter: str, amount: int = 1) -> None:
    with _LOCK:
        _COUNTERS[counter] = _COUNTERS.get(counter, 0) + amount


def _pct(vals: list[float], p: float) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    idx = min(len(s) - 1, max(0, int(round((len(s) - 1) * p))))
    return round(s[idx], 1)


def snapshot() -> dict:
    with _LOCK:
        lat = {
            k: {
                "count": len(dq),
                "p50_ms": _pct(list(dq), 0.50),
                "p95_ms": _pct(list(dq), 0.95),
            }
            for k, dq in _SAMPLES.items()
        }
        return {
            "uptime_s": round(time.time() - _STARTED_AT, 1),
            "counters": dict(_COUNTERS),
            "latency": lat,
        }
