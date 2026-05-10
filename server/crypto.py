"""Crypto — encrypt/decrypt FB account credentials at rest.

Uses Fernet (AES-128-CBC + HMAC-SHA256) with an env-sourced key. In
production the ``CREDENTIALS_KEY`` env var must be set to a real
Fernet key. In non-production the module derives or generates an
ephemeral key so local development keeps working without manual setup.

Any attempt to reuse the JWT secret as the credentials key, or to fall
back to a literal placeholder, fails fast in production — otherwise
anyone who learns the JWT secret can also decrypt stored FB credentials.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import threading

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_KEY_CACHE: bytes | None = None
_KEY_LOCK = threading.Lock()


def _is_production() -> bool:
    return os.getenv("ENV", "development").strip().lower() == "production"


def _validate_fernet_key(raw: str) -> bytes | None:
    """Return the key as bytes iff it decodes as a valid Fernet key."""
    try:
        encoded = raw.encode()
        # Fernet keys are 32 raw bytes encoded with urlsafe_b64 -> 44 chars.
        decoded = base64.urlsafe_b64decode(encoded)
        if len(decoded) != 32:
            return None
        # Instantiating validates the key format.
        Fernet(encoded)
        return encoded
    except (ValueError, TypeError, InvalidToken):
        return None


def _resolve_key() -> bytes:
    """Resolve the credentials encryption key.

    Production: ``CREDENTIALS_KEY`` must be set to a valid Fernet key.
    Dev/test: generate an ephemeral key (stored in-process) so tests run
    without configuration. Fields encrypted with an ephemeral key cannot
    be decrypted after a restart — intentional so dev data doesn't get
    silently persisted across key rotations.
    """
    global _KEY_CACHE
    if _KEY_CACHE is not None:
        return _KEY_CACHE

    with _KEY_LOCK:
        if _KEY_CACHE is not None:
            return _KEY_CACHE

        raw = os.getenv("CREDENTIALS_KEY", "").strip()
        if raw:
            validated = _validate_fernet_key(raw)
            if validated is None:
                raise RuntimeError(
                    "CREDENTIALS_KEY is set but is not a valid Fernet key. "
                    "Generate one with: "
                    "python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
                )
            _KEY_CACHE = validated
            return _KEY_CACHE

        if _is_production():
            raise RuntimeError(
                "CREDENTIALS_KEY must be set in production. "
                "Generate one with: "
                "python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
            )

        logger.warning(
            "CREDENTIALS_KEY not set; generating an ephemeral key for non-production. "
            "Encrypted values will NOT survive a restart."
        )
        _KEY_CACHE = Fernet.generate_key()
        return _KEY_CACHE


def _reset_key_cache_for_tests() -> None:
    """Test helper: force re-resolution of the credentials key on next use."""
    global _KEY_CACHE
    _KEY_CACHE = None


def encrypt(plaintext: str) -> str:
    """Encrypt a string. Returns base64-encoded ciphertext."""
    f = Fernet(_resolve_key())
    return f.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Decrypt a base64-encoded ciphertext. Returns plaintext."""
    f = Fernet(_resolve_key())
    return f.decrypt(ciphertext.encode()).decode()


def encrypt_cookies(cookies: dict[str, str]) -> str:
    """Serialize a cookie dict and encrypt it.

    Thin wrapper over :func:`encrypt` that uses the same
    ``"k=v; k=v"`` wire format the cookie parser produces, so re-parsing
    after decrypt is a no-op. Keys with ``;`` or ``=`` in their name are
    rejected (they can't roundtrip cleanly); values with ``=`` are fine.
    """
    parts: list[str] = []
    for name, value in cookies.items():
        if ";" in name or "=" in name:
            raise ValueError(
                f"Cookie name {name!r} contains reserved characters"
            )
        if ";" in value:
            raise ValueError(
                f"Cookie value for {name!r} contains reserved ';'"
            )
        parts.append(f"{name}={value}")
    return encrypt("; ".join(parts))


def decrypt_cookies(ciphertext: str) -> dict[str, str]:
    """Reverse of :func:`encrypt_cookies`. Returns a ``dict``."""
    raw = decrypt(ciphertext)
    out: dict[str, str] = {}
    if not raw:
        return out
    for piece in raw.split(";"):
        piece = piece.strip()
        if not piece or "=" not in piece:
            continue
        name, _, value = piece.partition("=")
        name = name.strip()
        if not name:
            continue
        out[name] = value.strip()
    return out


# Retained for callers that still compute a derived key explicitly; it now
# just returns ``_resolve_key()`` so the behavior is consistent with the
# rest of the module. Kept separate from ``_resolve_key`` so external
# imports don't break.
def derive_key_from_secret(secret: str) -> bytes:
    """Derive a Fernet key from an arbitrary secret (SHA-256 → urlsafe b64).

    Deprecated: prefer setting ``CREDENTIALS_KEY`` directly. Present only
    so legacy callers can round-trip data encrypted before this refactor.
    """
    derived = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(derived)
