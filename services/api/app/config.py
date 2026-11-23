from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root: sushrutai/  (this file is sushrutai/services/api/app/config.py)
ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT / ".env"), extra="ignore")

    sushrut_env: str = "dev"
    database_url: str = f"sqlite+aiosqlite:///{(ROOT / 'data' / 'sushrut.db').as_posix()}"
    storage_dir: Path = ROOT / "data" / "storage"
    master_key_b64: str = ""
    jwt_secret: str = "dev-only-insecure-secret-change-me"
    jwt_access_minutes: int = 15
    jwt_refresh_days: int = 30
    cors_origins: str = "http://localhost:3000"

    # local models
    ollama_base_url: str = "http://localhost:11434"
    llm_model: str = "medgemma:4b"
    # "Faster" option in Settings: a smaller general model for medicine / food / general questions. Symptom
    # messages and photos always use llm_model (the fast model under-judged urgency in eval/chat_eval.py,
    # 2026-11-21: 16/30 vs 24/30). Set FAST_MODEL_ENABLED=false to hide the option.
    # llama3.2:3b passed the chat gate (2026-11-22, eval/run_chat_candidates.sh): urgency 24/30 (MedGemma 24),
    # key facts 86% (>= 85%), safety 72/72, answer p50 24 s. qwen2.5:3b (16/30, 67%, 70/72) and qwen3:1.7b
    # (facts 76%) did not pass.
    fast_llm_model: str = "llama3.2:3b"
    fast_model_enabled: bool = True
    vlm_model: str = "medgemma:4b"
    rx_vlm_model: str = "qwen3-vl:4b-instruct"  # best on the MIRAGE Rx eval (2026-11-20)
    # Settings -> "Faster": smaller document readers, chosen per document type because handwriting and print differ
    # (eval/reports/fast_doc_model.json). Empty = none passed its gate; "Faster" then keeps rx_vlm_model.
    fast_rx_vlm_model: str = ""   # prescriptions
    # lab reports: qwen3-vl:2b was as accurate (91% tests found) but SLOWER on an idle machine (43 s vs 22 s a page,
    # eval/reports/speed_modes.json), so no faster lab reader is used.
    fast_lab_vlm_model: str = ""
    # Settings -> "Instant": the quickest models that passed their gates (eval/reports/fast_doc_model.json and the
    # chat eval). Empty = fall back to the "Faster" choice, then the default. Symptoms and photos always use the
    # careful medical model in every mode.
    # Suggestion chips: a tiny model on the CPU picks/rephrases rule-based suggestions ("" = rules only).
    # eval/reports/suggest.json (v2): qwen2.5:0.5b most relevant and fastest in English (~2 s on CPU);
    # gemma3:1b the only small model writing good Hindi (~4-5 s; the rule chips show meanwhile)
    suggest_model: str = "qwen2.5:0.5b"
    suggest_model_indic: str = "gemma3:1b"
    suggest_timeout_s: float = 3.0
    suggest_timeout_indic_s: float = 6.0
    instant_model_enabled: bool = True
    # Idle speeds (eval/reports/speed_modes.json): Careful 12.7 s, Faster 9.8 s, Instant 8.1 s median per answer.
    instant_llm_model: str = "llama3.2:3b"  # + 500-token answers and search without the reranker
    instant_rx_vlm_model: str = ""
    instant_lab_vlm_model: str = ""
    embed_model: str = "bge-m3"
    guard_model: str = "llama-guard3:1b"
    llm_timeout_s: float = 180.0

    # free / freemium APIs
    groq_api_key: str = ""
    groq_llm_model: str = "openai/gpt-oss-120b"
    groq_stt_model: str = "whisper-large-v3"
    sarvam_api_key: str = ""
    # Gemini (Google AI Studio key): the "Gemini" answer model. Flash-Lite has the larger free daily quota.
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    gemini_fast_model: str = "gemini-3.5-flash-lite"
    # Live API (WebSocket audio). Separate from the text chat models above.
    gemini_live_model: str = "gemini-3.8-live"
    gemini_flash_live_model: str = "gemini-3.1-flash-live-preview"  # legacy; Google recommends gemini-3.8-live
    gemini_transcribe_live_model: str = "gemini-3.5-transcribe-live"
    gemini_transcribe_model: str = "gemini-3.5-transcribe"
    gemini_translate_live_model: str = "gemini-3.5-live-translate-preview"
    gemini_tts_model: str = "gemini-3.8-flash-tts"
    gemini_tts_fallback_model: str = "gemini-3.8-flash-lite-tts"
    gemini_tts_voice: str = "Kore"
    # ElevenLabs: speech-to-text (Scribe) and read-aloud voices
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM"   # a stock multilingual voice; any voice id from your account
    elevenlabs_tts_model: str = "eleven_multilingual_v2"
    elevenlabs_stt_model: str = "scribe_v1"
    sarvam_tts_speaker: str = "anushka"
    # Ollama: how long models stay loaded after use (frees GPU memory sooner for the rarely used ones)
    keep_alive_chat: str = "10m"
    keep_alive_vision: str = "2m"
    speech_idle_unload_s: int = 600   # Python speech / translation models are released after this much idle time
    google_places_api_key: str = ""   # optional: fills missing hospital phone numbers (Google Places API, New)
    bhashini_user_id: str = ""
    bhashini_api_key: str = ""
    bhashini_pipeline_id: str = ""  # optional ULCA pipeline id; empty lets the config call pick a model
    openfda_api_key: str = ""
    nhs_api_key: str = ""  # free key from https://developer.api.nhs.uk (NHS website content API); KB ingest skips NHS without it

    # OTP
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    firebase_project_id: str = ""
    otp_ttl_s: int = 300
    otp_max_attempts: int = 5

    @property
    def data_dir(self) -> Path:
        return ROOT / "data"

    @property
    def is_dev(self) -> bool:
        return self.sushrut_env == "dev"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    Path(s.storage_dir).mkdir(parents=True, exist_ok=True)
    return s
