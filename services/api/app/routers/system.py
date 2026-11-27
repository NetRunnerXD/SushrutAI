from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db
from app.llm.providers import ollama
from app.models import Interaction, KbChunk, Product

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/providers")
async def providers(db: AsyncSession = Depends(get_db)):
    s = get_settings()
    models = await ollama().models()

    def has(m: str) -> bool:
        return m in models or f"{m}:latest" in models

    try:
        import faster_whisper  # noqa: F401
        whisper = True
    except ImportError:
        whisper = False
    try:
        import surya  # noqa: F401
        surya_ok = True
    except ImportError:
        surya_ok = False
    return {
        "local": {
            "ollama_running": bool(models), "ollama_models": models,
            "llm": {"model": s.llm_model, "available": has(s.llm_model)},
            "vlm": {"model": s.vlm_model, "available": has(s.vlm_model)},
            "rx_vlm": {"model": s.rx_vlm_model, "available": has(s.rx_vlm_model)},
            "embeddings": {"model": s.embed_model, "available": has(s.embed_model)},
            "stt_faster_whisper": whisper, "ocr_surya": surya_ok,
        },
        "free_apis": {"groq": bool(s.groq_api_key), "gemini": bool(s.gemini_api_key),
                      "gemini_live": bool(s.gemini_api_key),
                      "sarvam": bool(s.sarvam_api_key), "elevenlabs": bool(s.elevenlabs_api_key),
                      "bhashini": bool(s.bhashini_user_id and s.bhashini_api_key),
                      "openfda_key": bool(s.openfda_api_key),
                      "smtp": bool(s.smtp_host), "firebase_phone": bool(s.firebase_project_id)},
        "data": {
            "products": await db.scalar(select(func.count()).select_from(Product)),
            "janaushadhi_products": await db.scalar(select(func.count()).select_from(Product)
                                                    .where(Product.is_janaushadhi.is_(True))),
            "interactions": await db.scalar(select(func.count()).select_from(Interaction)),
            "kb_chunks": await db.scalar(select(func.count()).select_from(KbChunk)),
        },
    }


@router.get("/device")
async def device():
    """This computer and what is loaded now (Settings → "This computer")."""
    from app.runtime.device import profile, ram_free_fraction
    from app.speech import models as speech

    loaded = await ollama().running() if hasattr(ollama(), "running") else []
    return {**profile(), "ram_free_pct": round(100 * ram_free_fraction()), "speech_models_loaded": speech.loaded(),
            "ollama_loaded": loaded}


@router.get("/metrics")
async def metrics():
    """Lightweight non-PII latency and reliability telemetry."""
    from app.telemetry import snapshot

    return snapshot()
