"""Envelope encryption for PHI (DPDP: encryption at rest + crypto-shredding).

- A master key-encryption key (KEK) protects one data-encryption key (DEK) per user.
- PHI fields are AES-256-GCM encrypted with the user's DEK.
- Deleting a user's wrapped DEK makes all their ciphertext permanently unreadable.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import sys
from functools import lru_cache

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import get_settings

_NONCE = 12


def _dpapi(data: bytes, protect: bool) -> bytes | None:
    """Protect/unprotect bytes with Windows DPAPI (bound to the current Windows user)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        buf = ctypes.create_string_buffer(data, len(data))  # kept alive for the call
        src = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        out = BLOB()
        crypt32 = ctypes.windll.crypt32
        if protect:
            ok = crypt32.CryptProtectData(ctypes.byref(src), "SushrutAI", None, None, None, 0, ctypes.byref(out))
        else:
            ok = crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out))
        if not ok:
            return None
        res = ctypes.string_at(out.pbData, out.cbData)
        ctypes.windll.kernel32.LocalFree(out.pbData)
        return res
    except Exception:
        return None


@lru_cache
def master_key() -> bytes:
    s = get_settings()
    if s.master_key_b64:
        key = base64.b64decode(s.master_key_b64)
        if len(key) != 32:
            raise ValueError("MASTER_KEY_B64 must decode to 32 bytes")
        return key
    path = s.data_dir / "master.key"
    if path.exists():
        raw = path.read_bytes()
        if raw.startswith(b"DPAPI:"):
            key = _dpapi(raw[6:], protect=False)
            if key is None:
                raise RuntimeError("Could not unprotect master.key with DPAPI (different Windows user?)")
            return key
        return base64.b64decode(raw)
    key = AESGCM.generate_key(bit_length=256)
    protected = _dpapi(key, protect=True)
    path.write_bytes(b"DPAPI:" + protected if protected else base64.b64encode(key))
    return key


def _seal(key: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
    nonce = os.urandom(_NONCE)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, aad or None)


def _open(key: bytes, blob: bytes, aad: bytes = b"") -> bytes:
    return AESGCM(key).decrypt(blob[:_NONCE], blob[_NONCE:], aad or None)


def new_wrapped_dek() -> bytes:
    """Generate a per-user DEK and return it wrapped by the KEK."""
    return _seal(master_key(), AESGCM.generate_key(bit_length=256), b"dek")


def unwrap_dek(wrapped: bytes) -> bytes:
    return _open(master_key(), wrapped, b"dek")


class Cipher:
    """Per-user field cipher. aad binds ciphertext to the user id."""

    def __init__(self, wrapped_dek: bytes, user_id: str):
        self._dek = unwrap_dek(wrapped_dek)
        self._aad = user_id.encode()

    def enc(self, value: str | None) -> bytes | None:
        if value is None:
            return None
        return _seal(self._dek, value.encode("utf-8"), self._aad)

    def dec(self, blob: bytes | None) -> str | None:
        if blob is None:
            return None
        return _open(self._dek, blob, self._aad).decode("utf-8")

    def enc_bytes(self, data: bytes) -> bytes:
        return _seal(self._dek, data, self._aad)

    def dec_bytes(self, blob: bytes) -> bytes:
        return _open(self._dek, blob, self._aad)


def lookup_hash(value: str) -> str:
    """Deterministic keyed hash for looking up phone/email without storing plaintext."""
    norm = value.strip().lower()
    return hmac.new(master_key(), norm.encode(), hashlib.sha256).hexdigest()
