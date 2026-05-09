"""Authentication — JWT access/refresh tokens + RBAC.

Secret resolution is lazy and environment-aware:

* In production (``ENV=production``) both ``JWT_SECRET_KEY`` and any
  token-signing secret must be set to a non-default value. Missing or
  placeholder ("change-me") values raise ``RuntimeError`` on first use so
  the service refuses to sign trust-bearing tokens with a known constant.
* In development / test the module generates an ephemeral random secret
  and logs a warning. All tokens become invalid on restart, which is
  acceptable for local work and prevents silent use of ``"change-me"``.
"""

from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

security = HTTPBearer()
optional_security = HTTPBearer(auto_error=False)

JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
REFRESH_TOKEN_EXPIRE = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))

_PLACEHOLDER_SECRETS = {"", "change-me", "changeme", "your-secret", "secret"}
_JWT_SECRET_CACHE: str | None = None

# Bcrypt dummy hash used for constant-time failed-login responses.
# Computed at import so the timing of a missing-user path matches an
# existing-user path with a wrong password.
_DUMMY_PASSWORD_HASH = bcrypt.hashpw(b"__dummy__", bcrypt.gensalt()).decode("utf-8")


def _is_production() -> bool:
    return os.getenv("ENV", "development").strip().lower() == "production"


def _resolve_jwt_secret() -> str:
    """Resolve JWT secret with fail-fast semantics in production."""
    global _JWT_SECRET_CACHE
    if _JWT_SECRET_CACHE is not None:
        return _JWT_SECRET_CACHE

    configured = os.getenv("JWT_SECRET_KEY", "").strip()
    if configured.lower() in _PLACEHOLDER_SECRETS:
        if _is_production():
            raise RuntimeError(
                "JWT_SECRET_KEY must be set to a non-default value in production. "
                "Generate one with: python -c 'import secrets; print(secrets.token_urlsafe(64))'"
            )
        logger.warning(
            "JWT_SECRET_KEY not set; generating an ephemeral key for non-production. "
            "Tokens will be invalidated on process restart."
        )
        _JWT_SECRET_CACHE = secrets.token_urlsafe(64)
    else:
        _JWT_SECRET_CACHE = configured
    return _JWT_SECRET_CACHE


def _reset_jwt_secret_cache_for_tests() -> None:
    """Test helper: force re-resolution of the JWT secret on next use."""
    global _JWT_SECRET_CACHE
    _JWT_SECRET_CACHE = None


class Role(str, Enum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        # Malformed hash — treat as mismatch without leaking details.
        return False


def verify_password_constant_time(plain: str, hashed: str | None) -> bool:
    """Verify password against an optional hash in constant time.

    When ``hashed`` is ``None`` we still run bcrypt against a dummy hash so
    that the login path takes the same wall-clock time whether or not the
    user exists — preventing username enumeration via response timing.
    """
    candidate = hashed or _DUMMY_PASSWORD_HASH
    matched = verify_password(plain, candidate)
    return bool(hashed) and matched


def create_access_token(data: dict[str, Any]) -> str:
    payload = data.copy()
    payload["exp"] = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE)
    payload["type"] = "access"
    return jwt.encode(payload, _resolve_jwt_secret(), algorithm=JWT_ALGORITHM)


def create_refresh_token(data: dict[str, Any]) -> str:
    payload = data.copy()
    payload["exp"] = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE)
    payload["type"] = "refresh"
    return jwt.encode(payload, _resolve_jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, _resolve_jwt_secret(), algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired"
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict[str, Any]:
    payload = decode_token(credentials.credentials)
    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type"
        )
    return payload


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(optional_security),
) -> dict[str, Any] | None:
    """Return the caller's token payload if they present a valid access token.

    Unlike :func:`get_current_user` this returns ``None`` for anonymous
    requests and for malformed / expired tokens rather than raising. Use it
    on endpoints that must behave differently for authenticated callers (for
    example, ``/auth/register`` where the first user bootstraps without
    auth but subsequent registrations require admin).
    """
    if credentials is None:
        return None
    try:
        payload = decode_token(credentials.credentials)
    except HTTPException:
        return None
    if payload.get("type") != "access":
        return None
    return payload


def require_role(*roles: Role):
    """Dependency factory: allow request only if caller has one of ``roles``."""

    async def role_checker(user: dict[str, Any] = Depends(get_current_user)):
        user_role = user.get("role", "")
        allowed = [r.value for r in roles]
        if user_role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return user

    return role_checker
