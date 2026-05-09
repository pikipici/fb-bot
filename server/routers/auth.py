"""Auth router — login, refresh, register.

Registration rules:
* The first user bootstraps the system with role ``admin`` without auth.
* Every subsequent registration requires a valid ``Bearer`` token belonging
  to a caller with role ``admin``. The role field from the request is
  honored exactly (so admins can mint ``operator`` / ``viewer`` / ``admin``).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from server.auth import (
    Role,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
    get_optional_user,
    hash_password,
    verify_password_constant_time,
)
from server.database import get_db
from server.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth")

_GENERIC_LOGIN_ERROR = "Invalid credentials"


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    password: str
    role: str = "viewer"


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/login", response_model=TokenResponse)
async def login(request: LoginRequest, db: Session = Depends(get_db)):
    """Authenticate a user and return JWT access + refresh tokens.

    Uses a constant-time password comparison against a dummy hash when the
    user does not exist so response timing does not reveal username
    enumeration. A single generic error string covers both "user missing"
    and "wrong password"; ``is_active=False`` surfaces as the same generic
    error to avoid leaking account state.
    """
    user = db.query(User).filter(User.username == request.username).first()

    stored_hash = user.password_hash if user else None
    if not verify_password_constant_time(request.password, stored_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_GENERIC_LOGIN_ERROR,
        )

    # At this point the password matched. ``user`` is guaranteed non-None
    # because the constant-time helper returns False when stored_hash is None.
    assert user is not None  # for type-checker; never raises at runtime
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_GENERIC_LOGIN_ERROR,
        )

    token_data = {"sub": str(user.id), "username": user.username, "role": user.role}
    return TokenResponse(
        access_token=create_access_token(token_data),
        refresh_token=create_refresh_token(token_data),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(request: RefreshRequest, db: Session = Depends(get_db)):
    """Refresh access token using a valid refresh token."""
    payload = decode_token(request.refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
        )

    sub = payload.get("sub")
    try:
        user_id = int(sub)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or disabled",
        )

    token_data = {"sub": str(user.id), "username": user.username, "role": user.role}
    return TokenResponse(
        access_token=create_access_token(token_data),
        refresh_token=create_refresh_token(token_data),
    )


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    request: RegisterRequest,
    db: Session = Depends(get_db),
    caller: dict | None = Depends(get_optional_user),
):
    """Register a new user.

    * First ever user: no auth required, auto-promoted to ``admin``.
    * Afterwards: requires a valid access token from a role=admin caller.
    """
    user_count = db.query(User).count()
    is_first_user = user_count == 0

    if not is_first_user:
        if caller is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
            )
        if caller.get("role") != Role.ADMIN.value:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only admins can register new users",
            )

    # Check duplicate username
    existing = db.query(User).filter(User.username == request.username).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already exists",
        )

    # Validate role
    valid_roles = [r.value for r in Role]
    role = Role.ADMIN.value if is_first_user else request.role
    if role not in valid_roles:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role. Must be one of: {valid_roles}",
        )

    user = User(
        username=request.username,
        password_hash=hash_password(request.password),
        role=role,
    )
    db.add(user)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(user)

    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "message": "User created successfully",
    }


@router.get("/me")
async def get_me(user: dict = Depends(get_current_user)):
    """Get current authenticated user info."""
    return {
        "id": user.get("sub"),
        "username": user.get("username"),
        "role": user.get("role"),
    }
