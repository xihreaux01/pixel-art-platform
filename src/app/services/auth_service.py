"""Authentication service: password hashing, JWT tokens, user registration/login."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

import bcrypt
from fastapi import HTTPException, status
from jose import JWTError, jwt
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import User

# ---------------------------------------------------------------------------
# Password hashing helpers (bcrypt, cost 12)
# ---------------------------------------------------------------------------
# passlib 1.7.4 is incompatible with bcrypt >= 4.1 so we use bcrypt directly.

_BCRYPT_ROUNDS = 12


def hash_password(password: str) -> str:
    """Hash a plaintext password with bcrypt (cost 12)."""
    salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    return bcrypt.checkpw(
        plain_password.encode("utf-8"),
        hashed_password.encode("utf-8"),
    )


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    email: str = Field(..., max_length=320)
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=8)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        import re

        pattern = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
        if not re.match(pattern, v):
            raise ValueError("Invalid email address")
        return v.lower().strip()


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    user_id: UUID
    email: str
    username: str
    phone_verified: bool
    credit_balance: int
    created_at: datetime

    model_config = {"from_attributes": True}


class VerifyPhoneRequest(BaseModel):
    phone_number: str = Field(..., min_length=10, max_length=20)


# ---------------------------------------------------------------------------
# Service functions
# ---------------------------------------------------------------------------


async def register_user(db: AsyncSession, request: RegisterRequest) -> UserResponse:
    """Register a new user. Raises 409 if email/username already taken."""
    # Check for existing email
    result = await db.execute(select(User).where(User.email == request.email))
    if result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    # Check for existing username
    result = await db.execute(select(User).where(User.username == request.username))
    if result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already taken",
        )

    password_hashed = hash_password(request.password)

    user = User(
        email=request.email,
        username=request.username,
        password_hash=password_hashed,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)

    return UserResponse.model_validate(user)


async def authenticate_user(
    db: AsyncSession, email: str, password: str
) -> User | None:
    """Authenticate a user by email and password.

    SECURITY: Always performs a password hash even when the user does not exist
    to prevent timing-based user enumeration.
    """
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user is None:
        # Constant-time: hash the password anyway to prevent timing attacks
        hash_password(password)
        return None

    if not verify_password(password, user.password_hash):
        return None

    return user


def create_tokens(user_id: UUID, token_version: int) -> TokenResponse:
    """Create an access + refresh JWT token pair."""
    now = datetime.now(timezone.utc)

    access_payload = {
        "sub": str(user_id),
        "token_version": token_version,
        "type": "access",
        "exp": now + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES),
        "iat": now,
    }
    refresh_payload = {
        "sub": str(user_id),
        "token_version": token_version,
        "type": "refresh",
        "exp": now + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS),
        "iat": now,
    }

    access_token = jwt.encode(access_payload, settings.JWT_SECRET_KEY, algorithm="HS256")
    refresh_token = jwt.encode(refresh_payload, settings.JWT_SECRET_KEY, algorithm="HS256")

    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


def verify_token(token: str, token_type: str) -> dict:
    """Decode and validate a JWT token.

    Args:
        token: The JWT string.
        token_type: Expected type ("access" or "refresh").

    Returns:
        The decoded payload dict.

    Raises:
        HTTPException(401) if the token is invalid, expired, or the wrong type.
    """
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    if payload.get("type") != token_type:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Expected {token_type} token",
        )

    return payload


async def refresh_tokens(db: AsyncSession, refresh_token: str) -> TokenResponse:
    """Validate a refresh token and issue a new token pair.

    Checks that the user still exists and that token_version matches (i.e.
    tokens have not been revoked).
    """
    payload = verify_token(refresh_token, "refresh")

    user_id = UUID(payload["sub"])
    result = await db.execute(select(User).where(User.user_id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    if user.token_version != payload.get("token_version"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
        )

    return create_tokens(user.user_id, user.token_version)


async def revoke_all_tokens(db: AsyncSession, user_id: UUID) -> None:
    """Increment the user's token_version, invalidating all existing tokens."""
    result = await db.execute(select(User).where(User.user_id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    user.token_version += 1
    await db.flush()


async def verify_phone(db: AsyncSession, user_id: UUID, phone_number: str) -> dict:
    """Stub for phone verification.

    Hashes the phone number with SHA-256 and stores the hash. Actual SMS
    verification is TBD.
    """
    result = await db.execute(select(User).where(User.user_id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    phone_hash = hashlib.sha256(phone_number.encode("utf-8")).hexdigest()
    user.phone_hash = phone_hash
    user.phone_verified = True
    await db.flush()

    return {"status": "phone_verified", "detail": "Phone number verified (stub)"}
