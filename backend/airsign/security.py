"""Cryptography helpers (technical report, section 13.2)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import get_settings, master_key_bytes


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64u_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# Signed cookie values -------------------------------------------------------------


def _mac(key: str, msg: bytes) -> bytes:
    return hmac.new(key.encode(), msg, hashlib.sha256).digest()


def sign_value(value: str, purpose: str) -> str:
    body = b64u(f"{purpose}:{value}".encode())
    return f"{body}.{b64u(_mac(get_settings().secret_key, body.encode()))}"


def unsign_value(token: str | None, purpose: str) -> str | None:
    if not token or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    try:
        expected = _mac(get_settings().secret_key, body.encode())
        if not hmac.compare_digest(expected, b64u_decode(sig)):
            return None
        p, _, value = b64u_decode(body).decode().partition(":")
    except (ValueError, UnicodeDecodeError):
        return None
    return value if p == purpose else None


# Nonces -------------------------------------------------------------------------


def new_nonce() -> str:
    """128-bit nonce from a cryptographically secure generator, base64url."""
    return b64u(secrets.token_bytes(16))


def hash_nonce(nonce: str, intent_id: str) -> str:
    """Stored hashed and bound to the intent id."""
    return hashlib.sha256(f"{intent_id}:{nonce}".encode()).hexdigest()


# Envelope encryption for biometric templates ------------------------------------


def _master() -> AESGCM:
    return AESGCM(master_key_bytes(get_settings().master_key))


def seal(plaintext: bytes, aad: str) -> tuple[bytes, bytes]:
    """AES-256-GCM with a fresh data key, wrapped by the master key. Returns (wrapped_key, ciphertext)."""
    data_key = AESGCM.generate_key(bit_length=256)
    n1, n2 = os.urandom(12), os.urandom(12)
    ciphertext = n1 + AESGCM(data_key).encrypt(n1, plaintext, aad.encode())
    wrapped = n2 + _master().encrypt(n2, data_key, b"airsign-dek:" + aad.encode())
    return wrapped, ciphertext


def unseal(wrapped: bytes, ciphertext: bytes, aad: str) -> bytes:
    data_key = _master().decrypt(wrapped[:12], wrapped[12:], b"airsign-dek:" + aad.encode())
    return AESGCM(data_key).decrypt(ciphertext[:12], ciphertext[12:], aad.encode())


# Merchant PINs ------------------------------------------------------------------


def hash_pin(pin: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(pin.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"{b64u(salt)}${b64u(digest)}"


def check_pin(pin: str, stored: str) -> bool:
    salt, digest = stored.split("$")
    test = hashlib.scrypt(pin.encode(), salt=b64u_decode(salt), n=2**14, r=8, p=1)
    return hmac.compare_digest(test, b64u_decode(digest))


# Check-in tokens (section 11.4) -------------------------------------------------


def make_checkin_token(shop_secret: str, payload: dict) -> str:
    body = b64u(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    return f"{body}.{b64u(_mac(shop_secret, body.encode()))}"


def read_checkin_token(token: str) -> tuple[dict, str, str] | None:
    """Parse without verifying. Returns (payload, body, sig) so the caller can look up the shop secret."""
    try:
        body, sig = token.split(".")
        return json.loads(b64u_decode(body)), body, sig
    except (ValueError, json.JSONDecodeError):
        return None


def verify_checkin_signature(shop_secret: str, body: str, sig: str) -> bool:
    try:
        return hmac.compare_digest(_mac(shop_secret, body.encode()), b64u_decode(sig))
    except ValueError:
        return False
