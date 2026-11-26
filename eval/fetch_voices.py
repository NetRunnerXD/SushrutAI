"""Download the Meta MMS-TTS voices (CC-BY-NC-4.0) for every Indian language the app reads aloud.
Run: services/api/.venv/Scripts/python eval/fetch_voices.py   (shows progress; skips voices already on disk)"""
import os
import sys
import time

from huggingface_hub import snapshot_download

CACHE = os.getenv("SUSHRUT_HF_CACHE", "D:/hf/hub")
VOICES = ["hin", "ben", "mar", "tel", "tam", "guj", "kan", "mal", "ory", "pan", "asm", "urd-script_arabic"]

failed = []
for i, code in enumerate(VOICES, 1):
    repo = f"facebook/mms-tts-{code}"
    t0 = time.time()
    print(f"[{i}/{len(VOICES)}] {repo} ...", flush=True)
    try:
        path = snapshot_download(repo, cache_dir=CACHE, allow_patterns=["*.json", "*.safetensors", "*.txt", "*.model"])
        size = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(path) for f in fs) / 1e6
        print(f"      ok  {size:.0f} MB  {time.time() - t0:.0f}s", flush=True)
    except Exception as e:  # noqa: BLE001
        failed.append(code)
        print(f"      FAILED: {e}", flush=True)
print("done" if not failed else f"done, failed: {failed}", flush=True)
sys.exit(1 if failed else 0)
