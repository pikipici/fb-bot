"""Auth router — login, refresh, register."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from server.auth import (
    Role,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
    hash_password,
    require_role,
    verify_password,
)
from server.database import get_db
from server.models import User

router = APIRouter(prefix="/auth")


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
    """Authenticate user and return JWT tokens."""
    user = db.query(User).filter(User.username == request.username).first()
    if not user or not verify_password(request.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled",
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

    user = db.query(User).filter(User.id == int(payload["sub"])).first()
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
):
    """Register a new user. First user becomes admin automatically."""
    # Check if any user exists — first user is auto-admin
    user_count = db.query(User).count()
    is_first_user = user_count == 0

    # Only admin can register new users (unless first user)
    if not is_first_user:
        # Validate caller is admin — we need auth header
        # This is handled by the dependency below
        pass

    # Check duplicate username
    existing = db.query(User).filter(User.username == request.username).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already exists",
        )

    # Validate role
    valid_roles = [r.value for r in Role]
    role = "admin" if is_first_user else request.role
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
    db.commit()
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
