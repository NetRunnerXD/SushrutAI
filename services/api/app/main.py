import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.config import get_settings
from app.db import init_db
from app.routers import (
    auth, chat, consent, documents, family, medicines, prescriptions, profile, records, reports, system, triage, visit,
    assistant, care, home, search, today, voice,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    s = get_settings()
    if not s.is_dev and (len(s.jwt_secret) < 32 or "change-me" in s.jwt_secret or "insecure" in s.jwt_secret):
        raise RuntimeError("Set a strong JWT_SECRET (>= 32 random chars) before running outside dev")
    await init_db()
    yield


app = FastAPI(title="SushrutAI API", version=__version__, lifespan=lifespan,
              description="Voice-first, local-first health information assistant for India. "
                          "Not a substitute for professional medical advice.")
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in get_settings().cors_origins.split(",")],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def security_headers(request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    return resp


API = "/api/v1"
for r in (auth, consent, profile, family, records, visit, reports, documents, chat, prescriptions, medicines, triage, search,
          assistant, care, home, today,
          voice, system):
    app.include_router(r.router, prefix=API)


@app.get("/healthz")
async def healthz():
    return {"ok": True, "version": __version__}
