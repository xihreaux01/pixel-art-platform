"""Shared FastAPI dependencies for authenticated routes."""

from __future__ import annotations

from uuid import UUID

from fastapi import Cookie, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import User
from app.services.auth_service import verify_token

# Optional bearer scheme -- auto_error=False so we can fall back to cookies
_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    access_token: str | None = Cookie(default=None),
) -> User:
    """Extract and validate the access token, then return the User.

    Token sources (checked in order):
      1. Authorization: Bearer <token> header
      2. ``access_token`` cookie

    Raises HTTPException(401) if no valid token is found or the user does not
    exist / has been deactivated.
    """
    token: str | None = None

    if credentials is not None:
        token = credentials.credentials
    elif access_token is not None:
        token = access_token

    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = verify_token(token, "access")

    user_id = UUID(payload["sub"])
    result = await db.execute(select(User).where(User.user_id == user_id))
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or deactivated",
        )

    # Check token_version matches (tokens may have been revoked)
    if user.token_version != payload.get("token_version"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
        )

    return user
