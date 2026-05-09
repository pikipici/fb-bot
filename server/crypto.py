"""Crypto — encrypt/decrypt FB account credentials at rest."""

import os
from cryptography.fernet import Fernet


def _get_key() -> bytes:
    """Get encryption key from environment.

    Uses CREDENTIALS_KEY env var. If not set, derives from JWT_SECRET_KEY.
    """
    key = os.getenv("CREDENTIALS_KEY", "")
    if key:
        return key.encode()

    # Derive from JWT secret as fallback
    import hashlib
    import base64
    jwt_secret = os.getenv("JWT_SECRET_KEY", "fallback-insecure-key")
    derived = hashlib.sha256(jwt_secret.encode()).digest()
    return base64.urlsafe_b64encode(derived)


def encrypt(plaintext: str) -> str:
    """Encrypt a string. Returns base64-encoded ciphertext."""
    f = Fernet(_get_key())
    return f.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Decrypt a base64-encoded ciphertext. Returns plaintext."""
    f = Fernet(_get_key())
    return f.decrypt(ciphertext.encode()).decode()
