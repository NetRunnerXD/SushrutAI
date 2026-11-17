from __future__ import annotations

import asyncio
import logging
import smtplib
import time
from datetime import timedelta
from email.message import EmailMessage

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db
from app.models import OtpChallenge, User, _now as utcnow
from app.security.auth import decode_token, hash_otp, issue_tokens, new_otp
from app.security.crypto import Cipher, lookup_hash, new_wrapped_dek

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger("sushrut.auth")


class EmailOtpRequest(BaseModel):
    email: EmailStr
    lang: str = "en"


class OtpVerify(BaseModel):
    email: EmailStr
    code: str = Field(pattern=r"^\d{6}$")


class FirebaseLogin(BaseModel):
    id_token: str


class Refresh(BaseModel):
    refresh_token: str


def _new_user(**kw) -> User:
    return User(dek_wrapped=new_wrapped_dek(), **kw)


@router.post("/guest")
async def guest(db: AsyncSession = Depends(get_db)):
    u = _new_user(is_guest=True)
    db.add(u)
    await db.commit()
    return {"user_id": u.id, "is_guest": True, **issue_tokens(u.id)}


def _send_email(to: str, code: str, lang: str) -> None:
    s = get_settings()
    msg = EmailMessage()
    msg["From"] = s.smtp_from or s.smtp_user
    msg["To"] = to
    if lang == "hi":
        msg["Subject"] = "SushrutAI लॉगिन कोड"
        msg.set_content(f"आपका SushrutAI लॉगिन कोड {code} है। यह 5 मिनट में समाप्त हो जाएगा। इसे किसी से साझा न करें।")
    else:
        msg["Subject"] = "Your SushrutAI login code"
        msg.set_content(f"Your SushrutAI login code is {code}. It expires in 5 minutes. Do not share it with anyone.")
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
        smtp.starttls()
        if s.smtp_user:
            smtp.login(s.smtp_user, s.smtp_password)
        smtp.send_message(msg)


@router.post("/otp/request")
async def otp_request(body: EmailOtpRequest, db: AsyncSession = Depends(get_db)):
    s = get_settings()
    ident = lookup_hash(body.email)
    recent = await db.scalars(select(OtpChallenge).where(
        OtpChallenge.identifier_hash == ident,
        OtpChallenge.expires_at > utcnow() + timedelta(seconds=s.otp_ttl_s - 30)))
    if recent.first():
        raise HTTPException(429, "Please wait 30 seconds before requesting another code")
    code = new_otp()
    db.add(OtpChallenge(identifier_hash=ident, channel="email", code_hash=hash_otp(code, ident),
                        expires_at=utcnow() + timedelta(seconds=s.otp_ttl_s)))
    await db.commit()
    if s.smtp_host:
        await asyncio.to_thread(_send_email, body.email, code, body.lang)
        return {"sent": True, "channel": "email"}
    if s.is_dev:
        log.warning("SMTP not configured — dev OTP for %s is %s", body.email, code)
        return {"sent": False, "channel": "email", "dev_code": code,
                "note": "SMTP not configured; code returned only because SUSHRUT_ENV=dev"}
    raise HTTPException(503, "Email delivery is not configured")


@router.post("/otp/verify")
async def otp_verify(body: OtpVerify, db: AsyncSession = Depends(get_db)):
    s = get_settings()
    ident = lookup_hash(body.email)
    ch = (await db.scalars(select(OtpChallenge).where(
        OtpChallenge.identifier_hash == ident, OtpChallenge.consumed.is_(False)
    ).order_by(OtpChallenge.expires_at.desc()))).first()
    now = utcnow()
    if ch is None or ch.expires_at < now:
        raise HTTPException(400, "Code expired — request a new one")
    if ch.attempts >= s.otp_max_attempts:
        raise HTTPException(429, "Too many attempts — request a new code")
    ch.attempts += 1
    if ch.code_hash != hash_otp(body.code, ident):
        await db.commit()
        raise HTTPException(400, "Incorrect code")
    ch.consumed = True
    user = (await db.scalars(select(User).where(User.email_hash == ident))).first()
    if user is None:
        user = _new_user(email_hash=ident)
        db.add(user)
        await db.flush()
        user.email_enc = Cipher(user.dek_wrapped, user.id).enc(body.email)
    user.last_login_at = now
    await db.commit()
    return {"user_id": user.id, "is_guest": False, **issue_tokens(user.id)}


# --- Phone OTP via Firebase Authentication (free Spark-plan quota; Firebase handles Indian DLT SMS) ---
_GOOGLE_CERTS = "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"
_cert_cache: dict = {"at": 0.0, "certs": {}}


async def _firebase_certs() -> dict:
    if time.time() - _cert_cache["at"] > 3600:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(_GOOGLE_CERTS)
            r.raise_for_status()
            _cert_cache.update(at=time.time(), certs=r.json())
    return _cert_cache["certs"]


@router.post("/firebase")
async def firebase_login(body: FirebaseLogin, db: AsyncSession = Depends(get_db)):
    s = get_settings()
    if not s.firebase_project_id:
        raise HTTPException(503, "Phone login is not configured (FIREBASE_PROJECT_ID)")
    from cryptography.x509 import load_pem_x509_certificate

    kid = jwt.get_unverified_header(body.id_token).get("kid")
    pem = (await _firebase_certs()).get(kid)
    if not pem:
        raise HTTPException(401, "Unknown signing key")
    key = load_pem_x509_certificate(pem.encode()).public_key()
    try:
        claims = jwt.decode(body.id_token, key, algorithms=["RS256"], audience=s.firebase_project_id,
                            issuer=f"https://securetoken.google.com/{s.firebase_project_id}")
    except jwt.PyJWTError as e:
        raise HTTPException(401, f"Invalid Firebase token: {e}") from e
    phone = claims.get("phone_number")
    if not phone:
        raise HTTPException(400, "Token has no verified phone number")
    ident = lookup_hash(phone)
    user = (await db.scalars(select(User).where(User.phone_hash == ident))).first()
    if user is None:
        user = _new_user(phone_hash=ident)
        db.add(user)
        await db.flush()
        user.phone_enc = Cipher(user.dek_wrapped, user.id).enc(phone)
    user.last_login_at = utcnow()
    await db.commit()
    return {"user_id": user.id, "is_guest": False, **issue_tokens(user.id)}


@router.post("/refresh")
async def refresh(body: Refresh, db: AsyncSession = Depends(get_db)):
    uid = decode_token(body.refresh_token, "refresh")
    if await db.get(User, uid) is None:
        raise HTTPException(401, "User not found")
    return issue_tokens(uid)
