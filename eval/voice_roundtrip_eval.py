"""Round trip per language: text -> local TTS (MMS) -> audio -> local STT (IndicConformer) -> text.
Character error rate shows whether both directions work for every language the app speaks.
Run: services/api/.venv/Scripts/python eval/voice_roundtrip_eval.py [--langs pa,as,ur]"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "api"))
from app.speech import models as M  # noqa: E402

SENT = {
    "hi": "मुझे दो दिन से बुखार और सिर दर्द है",
    "bn": "আমার দুই দিন ধরে জ্বর আর মাথাব্যথা",
    "mr": "मला दोन दिवसांपासून ताप आणि डोकेदुखी आहे",
    "ta": "எனக்கு இரண்டு நாட்களாக காய்ச்சல் மற்றும் தலைவலி இருக்கிறது",
    "te": "నాకు రెండు రోజులుగా జ్వరం మరియు తలనొప్పి ఉంది",
    "gu": "મને બે દિવસથી તાવ અને માથાનો દુખાવો છે",
    "kn": "ನನಗೆ ಎರಡು ದಿನಗಳಿಂದ ಜ್ವರ ಮತ್ತು ತಲೆನೋವು ಇದೆ",
    "ml": "എനിക്ക് രണ്ട് ദിവസമായി പനിയും തലവേദനയും ഉണ്ട്",
    "or": "ମୋର ଦୁଇ ଦିନ ହେବ ଜ୍ୱର ଓ ମୁଣ୍ଡବିନ୍ଧା ହେଉଛି",
    "pa": "ਮੈਨੂੰ ਦੋ ਦਿਨਾਂ ਤੋਂ ਬੁਖ਼ਾਰ ਅਤੇ ਸਿਰ ਦਰਦ ਹੈ",
    "as": "মোৰ দুদিনৰ পৰা জ্বৰ আৰু মূৰৰ বিষ হৈছে",
    "ur": "مجھے دو دن سے بخار اور سر درد ہے",
}


def cer(ref: str, hyp: str) -> float:
    r, h = ref.replace(" ", ""), hyp.replace(" ", "")
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev, d[j] = d[j], cur
    return d[len(h)] / max(len(r), 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--langs", default=",".join(SENT))
    a = ap.parse_args()
    rows = []
    for lang in a.langs.split(","):
        t0 = time.time()
        wav_bytes, engine = M.tts(SENT[lang], lang)
        t_tts = time.time() - t0
        with wave.open(io.BytesIO(wav_bytes)) as w:
            sr = w.getframerate()
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        if sr != 16000:  # MMS speaks at 16 kHz; resample defensively
            pcm = np.interp(np.linspace(0, len(pcm), int(len(pcm) * 16000 / sr), endpoint=False), np.arange(len(pcm)), pcm)
        t1 = time.time()
        text = M.stt_indic(pcm.astype(np.float32), lang)
        t_stt = time.time() - t1
        row = {"lang": lang, "tts": engine, "tts_s": round(t_tts, 2), "stt_s": round(t_stt, 2),
               "audio_s": round(len(pcm) / 16000, 1), "cer": round(cer(SENT[lang], text), 3), "heard": text}
        rows.append(row)
        print(f"{lang}  CER {row['cer']:.2f}  tts {row['tts_s']}s  stt {row['stt_s']}s  heard: {text}", flush=True)
    out = Path(__file__).resolve().parent / "reports" / "voice_roundtrip.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), "utf-8")
    print("mean CER", round(sum(r["cer"] for r in rows) / len(rows), 3), "->", out.name)


if __name__ == "__main__":
    main()
