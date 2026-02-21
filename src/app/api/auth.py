"""Authentication API router -- /api/v1/auth/*."""

from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.api.dependencies import get_current_user
from app.models import User
from app.services.auth_service import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
    VerifyPhoneRequest,
    authenticate_user,
    create_tokens,
    refresh_tokens as refresh_tokens_service,
    register_user,
    revoke_all_tokens,
    verify_phone,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _set_token_cookies(response: Response, tokens: TokenResponse) -> None:
    """Set httpOnly, Secure, SameSite=Strict cookies for both tokens."""
    response.set_cookie(
        key="access_token",
        value=tokens.access_token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=15 * 60,  # 15 minutes
        path="/",
    )
    response.set_cookie(
        key="refresh_token",
        value=tokens.refresh_token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=7 * 24 * 60 * 60,  # 7 days
        path="/api/v1/auth",
    )


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    request: RegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Register a new user account."""
    return await register_user(db, request)


@router.post("/login", response_model=TokenResponse)
async def login(
    request: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Authenticate and return JWT tokens (also sets httpOnly cookies)."""
    user = await authenticate_user(db, request.email, request.password)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    tokens = create_tokens(user.user_id, user.token_version)
    _set_token_cookies(response, tokens)
    return tokens


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    response: Response,
    db: AsyncSession = Depends(get_db),
    refresh_token: str | None = Cookie(default=None),
) -> TokenResponse:
    """Refresh the token pair using the refresh_token cookie."""
    if refresh_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token missing",
        )

    tokens = await refresh_tokens_service(db, refresh_token)
    _set_token_cookies(response, tokens)
    return tokens


@router.post("/logout", status_code=status.HTTP_200_OK)
async def logout(
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Log out by revoking all tokens and clearing cookies."""
    await revoke_all_tokens(db, current_user.user_id)

    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/api/v1/auth")

    return {"detail": "Successfully logged out"}


@router.post("/verify-phone", status_code=status.HTTP_200_OK)
async def verify_phone_endpoint(
    request: VerifyPhoneRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Stub endpoint for phone number verification."""
    return await verify_phone(db, current_user.user_id, request.phone_number)
