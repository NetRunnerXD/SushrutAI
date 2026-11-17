from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import current_account_id, current_user_id, get_db
from app.models import HouseholdMember, User
from app.security.crypto import Cipher

_bearer = HTTPBearer(auto_error=False)


def issue_tokens(user_id: str) -> dict:
    s = get_settings()
    now = datetime.now(timezone.utc)
    access = jwt.encode(
        {"sub": user_id, "typ": "access", "iat": now, "exp": now + timedelta(minutes=s.jwt_access_minutes)},
        s.jwt_secret,
        algorithm="HS256",
    )
    refresh = jwt.encode(
        {
            "sub": user_id,
            "typ": "refresh",
            "jti": secrets.token_hex(8),
            "iat": now,
            "exp": now + timedelta(days=s.jwt_refresh_days),
        },
        s.jwt_secret,
        algorithm="HS256",
    )
    return {"access_token": access, "refresh_token": refresh, "token_type": "bearer",
            "expires_in": s.jwt_access_minutes * 60}


def decode_token(token: str, typ: str) -> str:
    try:
        payload = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token") from e
    if payload.get("typ") != typ:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong token type")
    return payload["sub"]


@dataclass
class CurrentUser:
    """`user` is whose health data this request acts on (the account holder, or a family member they manage);
    `account` is the logged-in account holder, who owns consents, privacy settings and the family list."""
    user: User
    cipher: Cipher
    account: User | None = None

    @property
    def id(self) -> str:
        return self.user.id

    @property
    def account_id(self) -> str:
        return (self.account or self.user).id

    @property
    def is_member(self) -> bool:
        return self.account is not None and self.account.id != self.user.id

    @property
    def account_cipher(self) -> Cipher:
        return self.cipher if not self.is_member else Cipher(self.account.dek_wrapped, self.account.id)


async def current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    x_profile_id: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    uid = decode_token(creds.credentials, "access")
    pid = x_profile_id if x_profile_id and x_profile_id != uid else None
    # Postgres row-level security scope for this request (see app/db.py). Set before the first query.
    current_user_id.set(pid or uid)
    current_account_id.set(uid)
    account = await db.get(User, uid)
    if account is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    if pid is None:
        return CurrentUser(user=account, cipher=Cipher(account.dek_wrapped, account.id), account=account)
    link = (await db.scalars(select(HouseholdMember).where(HouseholdMember.user_id == uid,
                                                           HouseholdMember.member_id == pid))).first()
    member = await db.get(User, pid) if link else None
    if member is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a profile you manage")
    return CurrentUser(user=member, cipher=Cipher(member.dek_wrapped, member.id), account=account)


def hash_otp(code: str, identifier_hash: str) -> str:
    return hmac.new(get_settings().jwt_secret.encode(), f"{identifier_hash}:{code}".encode(),
                    hashlib.sha256).hexdigest()


def new_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"
