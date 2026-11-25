"""What this computer has (RAM, GPU memory, CPU cores), and the memory housekeeping that follows from it.

- `profile()` is read once at start (and shown in Settings → Answer speed → "This computer").
- `housekeeping()` runs every minute: speech / translation models idle for SPEECH_IDLE_UNLOAD_S are released,
  sooner (after a quarter of that time) when free RAM drops below 15 %. Ollama frees its own models after their
  keep_alive (10 min for chat models, 2 min for the vision readers: see app/llm/providers.py).
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
from functools import lru_cache

log = logging.getLogger("sushrut.runtime")


@lru_cache(maxsize=1)
def profile() -> dict:
    out = {"cpu_cores": os.cpu_count() or 1, "ram_gb": None, "gpu": None, "vram_gb": None}
    try:
        import psutil

        out["ram_gb"] = round(psutil.virtual_memory().total / 1e9, 1)
    except Exception:  # noqa: BLE001
        pass
    if shutil.which("nvidia-smi"):
        try:
            r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                               capture_output=True, text=True, timeout=5)
            name, mem = r.stdout.strip().splitlines()[0].rsplit(",", 1)
            out["gpu"], out["vram_gb"] = name.strip(), round(int(mem) / 1024, 1)
        except Exception:  # noqa: BLE001
            pass
    return out


def ram_free_fraction() -> float:
    try:
        import psutil

        vm = psutil.virtual_memory()
        return vm.available / vm.total
    except Exception:  # noqa: BLE001
        return 1.0


async def housekeeping(stop: asyncio.Event) -> None:
    from app.config import get_settings
    from app.speech import models as speech

    idle = get_settings().speech_idle_unload_s
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=60)
        except asyncio.TimeoutError:
            pass
        if stop.is_set():
            break
        try:
            limit = idle / 4 if ram_free_fraction() < 0.15 else idle
            await asyncio.to_thread(speech.release_idle, limit)
        except Exception:  # noqa: BLE001 — housekeeping never takes the app down
            log.exception("model housekeeping failed")
