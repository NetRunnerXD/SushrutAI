"""Spoken-language detection for the mic and hands-free mode: which language is the person speaking?

Clips: English and Hindi sentences a patient might say (short ones too), synthesised with the local MMS voices
(facebook/mms-tts-eng / -hin) and given light background noise (SNR 20 dB). Each clip is scored with the app
set to English and set to Hindi:
  before      the old behaviour: the app language is assumed (Hindi spoken to the English app is wrong)
  whisper     Whisper's own detection over all ~100 languages (calls Hindi "Urdu", etc.)
  after       detect_spoken_language: restricted to {app language, English, Hindi}, Urdu->Hindi, short/unsure
              clips keep the app language
    cd services/api && .venv/Scripts/python -I -X utf8 ../../eval/lang_detect_eval.py
Writes eval/reports/lang_detect.json. Synthetic speech is cleaner than a real phone mic: treat as a lower bound
on errors, and re-check with real recordings.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

EN = ["I have had a fever since yesterday", "My head hurts a lot", "What should I eat for lunch",
      "Is my blood pressure normal", "I feel dizzy when I stand up", "My child has a cough for three days",
      "Can I take this medicine after food", "I have pain in my lower back", "Show my reports",
      "My sugar was one hundred and forty this morning", "I could not sleep last night",
      "There is a burning feeling in my chest after meals", "Remind me to take my tablets",
      "Yes", "Stop", "Thank you", "How are you", "Read it again", "My knee hurts when I climb stairs",
      "I am eight weeks pregnant and feel sick every morning"]
HI = ["मुझे कल से बुखार है", "मेरे सिर में बहुत दर्द है", "दोपहर में क्या खाना चाहिए", "क्या मेरा बीपी ठीक है",
      "खड़े होने पर चक्कर आता है", "मेरे बच्चे को तीन दिन से खांसी है", "क्या यह दवाई खाने के बाद ले सकते हैं",
      "कमर के निचले हिस्से में दर्द है", "मेरी रिपोर्ट दिखाओ", "आज सुबह मेरी शुगर एक सौ चालीस थी",
      "कल रात नींद नहीं आई", "खाने के बाद सीने में जलन होती है", "मुझे गोली लेने की याद दिलाना",
      "हाँ", "रुको", "धन्यवाद", "आप कैसे हैं", "फिर से पढ़ो", "सीढ़ियां चढ़ने पर घुटने में दर्द होता है",
      "मैं आठ हफ्ते की गर्भवती हूं और हर सुबह जी मिचलाता है"]


def synth(texts: list[str], code: str) -> list[np.ndarray]:
    import torch
    from transformers import AutoTokenizer, VitsModel

    from app.speech.models import TTS_CACHE
    name = f"facebook/mms-tts-{code}"
    tok = AutoTokenizer.from_pretrained(name, cache_dir=TTS_CACHE)
    model = VitsModel.from_pretrained(name, cache_dir=TTS_CACHE).eval()
    out = []
    rng = np.random.default_rng(0)
    for t in texts:
        with torch.no_grad():
            wav = model(**tok(t, return_tensors="pt")).waveform.squeeze().numpy().astype(np.float32)
        sr = model.config.sampling_rate
        if sr != 16000:
            import librosa
            wav = librosa.resample(wav, orig_sr=sr, target_sr=16000)
        noise = rng.normal(0, np.sqrt(np.mean(wav ** 2) / 100), wav.shape).astype(np.float32)  # SNR 20 dB
        out.append(np.concatenate([np.zeros(4000, np.float32), wav + noise, np.zeros(4000, np.float32)]))
    return out


def main():
    from app.speech import models as M

    t0 = time.time()
    clips = [(t, "en", a) for t, a in zip(EN, synth(EN, "eng"))] + [(t, "hi", a) for t, a in zip(HI, synth(HI, "hin"))]
    print(f"{len(clips)} clips synthesised in {time.time() - t0:.0f}s", flush=True)
    rows = []
    for text, truth, audio in clips:
        _, _, probs = M._lid().detect_language(audio[: 16000 * 10])
        raw = probs[0][0]
        for ui in ("en", "hi"):
            t1 = time.perf_counter()
            after, conf = M.detect_spoken_language(audio, [ui, "en", "hi"], fallback=ui)
            ms = round((time.perf_counter() - t1) * 1000)
            rows.append({"text": text, "truth": truth, "ui": ui, "secs": round(len(audio) / 16000, 1),
                         "before": ui, "whisper": raw, "after": after, "confidence": conf, "ms": ms})
            r = rows[-1]
            print(f"{truth} ui={ui} {r['secs']:4}s before={'ok ' if ui == truth else 'BAD'} whisper={raw:3}"
                  f"{'ok ' if raw == truth else 'BAD'} after={after}{'ok ' if after == truth else 'BAD'} "
                  f"conf={conf:.2f} {ms}ms | {text[:40]}", flush=True)
    acc = lambda k, rs: round(100 * sum(r[k] == r["truth"] for r in rs) / len(rs), 1)  # noqa: E731
    mism = [r for r in rows if r["ui"] != r["truth"]]
    summary = {"clips": len(clips), "scored": len(rows),
               "before_pct": acc("before", rows), "whisper_pct": acc("whisper", rows), "after_pct": acc("after", rows),
               "spoken_differs_from_app_before_pct": acc("before", mism),
               "spoken_differs_from_app_after_pct": acc("after", mism),
               "short_clips_after_pct": acc("after", [r for r in rows if r["secs"] < 2.5]),
               "median_ms": sorted(r["ms"] for r in rows)[len(rows) // 2]}
    out = ROOT / "eval" / "reports" / "lang_detect.json"
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    print(f"Report: {out}")


if __name__ == "__main__":
    main()
