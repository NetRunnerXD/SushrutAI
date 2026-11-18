"""Lazy, thread-safe loaders for the local speech/translation models (all open-weight)."""
from __future__ import annotations

import io
import logging
import os
import re
import threading
import time
from functools import lru_cache

import numpy as np

log = logging.getLogger("sushrut.speech")
_lock = threading.Lock()

DEVICE = os.getenv("SPEECH_DEVICE", "cpu")
TTS_CACHE = os.getenv("SUSHRUT_HF_CACHE", "D:/hf/hub")  # Parler / Whisper-turbo were downloaded here

# SushrutAI language code -> IndicTrans2 / FLORES code, IndicConformer code, Parler-TTS speaker
LANGS = {
    "hi": ("hin_Deva", "hi", "Rohit"), "bn": ("ben_Beng", "bn", "Arjun"), "ta": ("tam_Taml", "ta", "Jaya"),
    "te": ("tel_Telu", "te", "Prakash"), "mr": ("mar_Deva", "mr", "Sanjay"), "gu": ("guj_Gujr", "gu", "Yash"),
    "kn": ("kan_Knda", "kn", "Suresh"), "ml": ("mal_Mlym", "ml", "Anjali"), "or": ("ory_Orya", "or", "Manas"),
    "en": ("eng_Latn", None, "Thoma"),
}


def available(module: str) -> bool:
    try:
        __import__(module)
        return True
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------- audio decoding
def decode_audio(data: bytes, sr: int = 16000) -> np.ndarray:
    """Decode webm/opus/ogg/wav/mp3 bytes to mono float32 at `sr` Hz using PyAV (bundled FFmpeg)."""
    import av

    chunks = []
    with av.open(io.BytesIO(data), mode="r") as container:
        stream = next(s for s in container.streams if s.type == "audio")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=sr)
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):  # flush
            chunks.append(out.to_ndarray().reshape(-1))
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return (np.concatenate(chunks).astype(np.float32) / 32768.0)


# ---------------------------------------------------------------- IndicTrans2
@lru_cache(maxsize=2)
def _it2(direction: str):
    import torch
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

    name = {"indic-en": "ai4bharat/indictrans2-indic-en-dist-200M",
            "en-indic": "ai4bharat/indictrans2-en-indic-dist-200M"}[direction]
    t0 = time.time()
    tok = AutoTokenizer.from_pretrained(name, trust_remote_code=True)
    model = AutoModelForSeq2SeqLM.from_pretrained(name, trust_remote_code=True).to(DEVICE).eval()
    torch.set_num_threads(max(2, (os.cpu_count() or 8) // 2))
    log.info("IndicTrans2 %s loaded in %.1fs", direction, time.time() - t0)
    return tok, model


_SENT_SPLIT = re.compile(r"(?<=[.!?।॥])\s+")


def translate(text: str, src: str, tgt: str, max_len: int = 256) -> str:
    """Translate between English and an Indian language (native script). Long text is split by sentence."""
    import torch

    if src == tgt or not text.strip():
        return text
    direction = "indic-en" if tgt == "en" else "en-indic"
    s_code, t_code = LANGS[src][0], LANGS[tgt][0]
    sents = [s for s in _SENT_SPLIT.split(text.strip()) if s.strip()][:24]
    with _lock:
        tok, model = _it2(direction)
        batch = tok([f"{s_code} {t_code} {s}" for s in sents], return_tensors="pt", padding=True,
                    truncation=True, max_length=max_len).to(DEVICE)
        with torch.no_grad():
            out = model.generate(**batch, num_beams=1, max_length=max_len)
        return " ".join(tok.batch_decode(out, skip_special_tokens=True))


def is_native_script(text: str) -> bool:
    """True if the text is mostly in an Indic script (IndicTrans2 needs native script, not romanised Hinglish)."""
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and sum(1 for c in letters if "\u0900" <= c <= "\u0dff") / len(letters) > 0.5


# ---------------------------------------------------------------- STT
@lru_cache(maxsize=1)
def _conformer():
    from transformers import AutoModel

    t0 = time.time()
    m = AutoModel.from_pretrained("ai4bharat/indic-conformer-600m-multilingual", trust_remote_code=True)
    log.info("IndicConformer loaded in %.1fs", time.time() - t0)
    return m


@lru_cache(maxsize=1)
def _whisper():
    from faster_whisper import WhisperModel

    size = os.getenv("WHISPER_MODEL", "large-v3-turbo")
    root = os.path.join(TTS_CACHE, "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo", "snapshots")
    path = size
    if size == "large-v3-turbo" and os.path.isdir(root) and os.listdir(root):
        path = os.path.join(root, os.listdir(root)[0])
    t0 = time.time()
    m = WhisperModel(path, device=DEVICE, compute_type="int8", cpu_threads=max(4, (os.cpu_count() or 8) // 2))
    log.info("faster-whisper %s loaded in %.1fs", size, time.time() - t0)
    return m


DRUG_HOTWORDS = ("paracetamol, Dolo, Crocin, pantoprazole, Pan-D, azithromycin, amoxicillin, metformin, "
                 "glimepiride, telmisartan, amlodipine, atorvastatin, cetirizine, montelukast, ORS")


def stt_indic(audio: np.ndarray, lang: str) -> str:
    import torch

    code = LANGS[lang][1]
    with _lock:
        m = _conformer()
        wav = torch.from_numpy(audio).unsqueeze(0)
        return str(m(wav, code, "ctc")).strip()


def stt_whisper(audio: np.ndarray, lang: str | None) -> tuple[str, str]:
    with _lock:
        segs, info = _whisper().transcribe(audio, language=lang, vad_filter=True, beam_size=5,
                                           initial_prompt=DRUG_HOTWORDS)
        return " ".join(s.text.strip() for s in segs).strip(), info.language


@lru_cache(maxsize=1)
def _lid():
    """A small Whisper used only to tell which language is spoken (~1 s on CPU; large-v3-turbo takes ~8 s)."""
    from faster_whisper import WhisperModel

    t0 = time.time()
    m = WhisperModel(os.getenv("WHISPER_LID_MODEL", "small"), device=DEVICE, compute_type="int8",
                     cpu_threads=max(4, (os.cpu_count() or 8) // 2))
    log.info("language-ID whisper loaded in %.1fs", time.time() - t0)
    return m


# Whisper sometimes labels Indian speech with a neighbouring language; these count as the Indian one.
LANG_ALIASES = {"ur": "hi", "ne": "hi", "pa": "hi", "sa": "hi", "as": "bn", "si": "ta"}
MIN_DETECT_S = 1.2   # shorter clips do not carry enough evidence: keep the app's language
MIN_CONF = 0.55      # share of the allowed languages' probability the winner needs


def detect_spoken_language(audio: np.ndarray, allowed: list[str], fallback: str, sr: int = 16000) -> tuple[str, float]:
    """Which of the allowed languages is being spoken -> (lang, confidence 0..1).

    Restricting the choice to the languages this person uses (the app language, English, Hindi) avoids the
    usual failure: Whisper calling Hindi "Urdu" or a short English phrase "Welsh". Short or unclear clips keep
    the app language."""
    if len(audio) < sr * MIN_DETECT_S:
        return fallback, 0.0
    with _lock:
        _, _, probs = _lid().detect_language(audio[: sr * 10])
    p: dict[str, float] = {}
    for code, v in probs:
        code = LANG_ALIASES.get(code, code)
        p[code] = p.get(code, 0.0) + v
    scores = {l: p.get(l, 0.0) for l in dict.fromkeys(allowed)}
    total = sum(scores.values())
    if total <= 0:
        return fallback, 0.0
    best = max(scores, key=scores.get)
    conf = scores[best] / total
    return (best, round(conf, 3)) if conf >= MIN_CONF else (fallback, round(conf, 3))


# ---------------------------------------------------------------- TTS
@lru_cache(maxsize=1)
def _parler():
    import torch
    from parler_tts import ParlerTTSForConditionalGeneration
    from transformers import AutoTokenizer

    name = "ai4bharat/indic-parler-tts"
    t0 = time.time()
    kw = {"cache_dir": TTS_CACHE} if os.path.isdir(TTS_CACHE) else {}
    model = ParlerTTSForConditionalGeneration.from_pretrained(name, **kw).to(DEVICE).eval()
    tok = AutoTokenizer.from_pretrained(name, **kw)
    desc_tok = AutoTokenizer.from_pretrained(model.config.text_encoder._name_or_path, **kw)
    torch.set_num_threads(max(4, (os.cpu_count() or 8) // 2))
    log.info("Indic Parler-TTS loaded in %.1fs", time.time() - t0)
    return model, tok, desc_tok


MMS_CODES = {"hi": "hin", "bn": "ben", "ta": "tam", "te": "tel", "mr": "mar", "gu": "guj", "kn": "kan",
             "ml": "mal", "or": "ory"}  # no English: the device's own voice reads English (on-device TTS)
DIGITS = {
    "hi": "शून्य एक दो तीन चार पाँच छह सात आठ नौ", "mr": "शून्य एक दोन तीन चार पाच सहा सात आठ नऊ",
    "bn": "শূন্য এক দুই তিন চার পাঁচ ছয় সাত আট নয়", "gu": "શૂન્ય એક બે ત્રણ ચાર પાંચ છ સાત આઠ નવ",
    "ta": "பூஜ்யம் ஒன்று இரண்டு மூன்று நான்கு ஐந்து ஆறு ஏழு எட்டு ஒன்பது",
    "te": "సున్నా ఒకటి రెండు మూడు నాలుగు ఐదు ఆరు ఏడు ఎనిమిది తొమ్మిది",
    "kn": "ಸೊನ್ನೆ ಒಂದು ಎರಡು ಮೂರು ನಾಲ್ಕು ಐದು ಆರು ಏಳು ಎಂಟು ಒಂಬತ್ತು",
    "ml": "പൂജ്യം ഒന്ന് രണ്ട് മൂന്ന് നാല് അഞ്ച് ആറ് ഏഴ് എട്ട് ഒമ്പത്",
    "or": "ଶୂନ ଏକ ଦୁଇ ତିନି ଚାରି ପାଞ୍ଚ ଛଅ ସାତ ଆଠ ନଅ",
}
UNITS = {"hi": {"mg": "मिलीग्राम", "ml": "मिलीलीटर", "%": "प्रतिशत"},
         "mr": {"mg": "मिलीग्रॅम", "ml": "मिलीलीटर"}}


def normalise_for_tts(text: str, lang: str) -> str:
    """MMS voices only know their own script: spell digits as words, localise units, drop Latin text."""
    if lang == "en":
        return text
    for unit, word in UNITS.get(lang, {}).items():
        text = re.sub(rf"(?i)(\d)\s*{re.escape(unit)}\b", rf"\1 {word}", text)
    words = DIGITS.get(lang, "").split()
    if words:
        text = re.sub(r"\d", lambda m: f" {words[int(m.group(0))]} ", text)
    text = re.sub(r"[A-Za-z][A-Za-z0-9\-]*", " ", text)  # Latin words cannot be voiced by the Indic MMS models
    return re.sub(r"\s+", " ", text).strip()


@lru_cache(maxsize=4)
def _mms(lang: str):
    from transformers import AutoTokenizer, VitsModel

    name = f"facebook/mms-tts-{MMS_CODES[lang]}"
    kw = {"cache_dir": TTS_CACHE} if os.path.isdir(TTS_CACHE) else {}
    t0 = time.time()
    model = VitsModel.from_pretrained(name, **kw).to(DEVICE).eval()
    tok = AutoTokenizer.from_pretrained(name, **kw)
    log.info("MMS-TTS %s loaded in %.1fs", name, time.time() - t0)
    return model, tok


def parler_enabled() -> bool:
    return os.getenv("TTS_ENGINE", "mms") == "parler"


def tts(text: str, lang: str) -> tuple[bytes, str]:
    """Synthesise speech -> (WAV bytes, engine). MMS-TTS by default (fast on CPU, CC BY-NC 4.0);
    Indic Parler-TTS (Apache-2.0, gated on Hugging Face) when TTS_ENGINE=parler and the model is accessible."""
    import soundfile as sf
    import torch

    if lang == "en":
        raise ValueError("English is read by the device's own voice")
    if parler_enabled():
        return _tts_parler(text, lang), "indic-parler-tts"
    lang = lang if lang in MMS_CODES else "hi"
    clean = normalise_for_tts(text, lang)[:800]
    if not clean:
        raise ValueError("Nothing speakable in this language")
    with _lock:
        model, tok = _mms(lang)
        inputs = tok(clean, return_tensors="pt").to(DEVICE)
        if inputs["input_ids"].numel() == 0:
            raise ValueError("Nothing speakable in this language after normalisation")
        with torch.no_grad():
            wav = model(**inputs).waveform
    buf = io.BytesIO()
    sf.write(buf, wav.cpu().numpy().squeeze(), model.config.sampling_rate, format="WAV")
    return buf.getvalue(), f"mms-tts-{MMS_CODES[lang]}"


def _tts_parler(text: str, lang: str) -> bytes:
    import soundfile as sf
    import torch

    speaker = LANGS.get(lang, LANGS["hi"])[2]
    description = (f"{speaker} speaks at a moderate pace with a calm, clear and warm voice. "
                   "The recording is of very high quality with no background noise.")
    with _lock:
        model, tok, desc_tok = _parler()
        d = desc_tok(description, return_tensors="pt").to(DEVICE)
        p = tok(text[:600], return_tensors="pt").to(DEVICE)
        with torch.no_grad():
            audio = model.generate(input_ids=d.input_ids, attention_mask=d.attention_mask,
                                   prompt_input_ids=p.input_ids, prompt_attention_mask=p.attention_mask)
    buf = io.BytesIO()
    sf.write(buf, audio.cpu().numpy().squeeze(), model.config.sampling_rate, format="WAV")
    return buf.getvalue()
