"""Tests for the authentication service and API endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
import jwt

from app.config import settings
from app.services.auth_service import (
    RegisterRequest,
    TokenResponse,
    UserResponse,
    create_tokens,
    hash_password,
    verify_password,
    verify_token,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEST_SECRET = "test-secret-key-for-unit-tests"


def _make_fake_user(
    user_id: uuid.UUID | None = None,
    email: str = "test@example.com",
    username: str = "testuser",
    password: str = "securepassword123",
    token_version: int = 0,
    is_active: bool = True,
    phone_verified: bool = False,
    credit_balance: int = 0,
) -> MagicMock:
    """Build a MagicMock that quacks like a User ORM instance."""
    user = MagicMock()
    user.user_id = user_id or uuid.uuid4()
    user.email = email
    user.username = username
    user.password_hash = hash_password(password)
    user.token_version = token_version
    user.is_active = is_active
    user.phone_verified = phone_verified
    user.credit_balance = credit_balance
    user.created_at = datetime.now(timezone.utc)
    return user


def _build_scalar_result(value):
    """Return a mock SQLAlchemy result whose scalar_one_or_none() returns *value*."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


# ---------------------------------------------------------------------------
# 1. Password hashing
# ---------------------------------------------------------------------------


def test_password_hashing():
    """hash_password + verify_password round-trip should succeed."""
    password = "my-very-secure-p@ssword!"
    hashed = hash_password(password)

    assert hashed != password, "Hash must differ from plaintext"
    assert hashed.startswith("$2"), "Should be a bcrypt hash"
    assert verify_password(password, hashed) is True
    assert verify_password("wrong-password", hashed) is False


# ---------------------------------------------------------------------------
# 2. Constant-time auth (nonexistent user still hashes)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_constant_time_auth():
    """authenticate_user must hash the password even when the user does not exist."""
    from app.services.auth_service import authenticate_user

    db = AsyncMock()
    # Simulate no user found
    db.execute.return_value = _build_scalar_result(None)

    with patch("app.services.auth_service.hash_password") as mock_hash:
        mock_hash.return_value = "$2b$12$fakehashvalue"
        result = await authenticate_user(db, "nonexistent@example.com", "anypass")

    assert result is None
    mock_hash.assert_called_once_with("anypass")


# ---------------------------------------------------------------------------
# 3. Token creation
# ---------------------------------------------------------------------------


def test_create_tokens():
    """create_tokens should produce valid JWTs with correct claims."""
    user_id = uuid.uuid4()
    token_version = 3

    with patch.object(settings, "JWT_SECRET_KEY", _TEST_SECRET):
        tokens = create_tokens(user_id, token_version)

    assert isinstance(tokens, TokenResponse)
    assert tokens.token_type == "bearer"

    # Decode access token
    access_payload = jwt.decode(tokens.access_token, _TEST_SECRET, algorithms=["HS256"])
    assert access_payload["sub"] == str(user_id)
    assert access_payload["token_version"] == token_version
    assert access_payload["type"] == "access"
    assert "exp" in access_payload
    assert "iat" in access_payload

    # Decode refresh token
    refresh_payload = jwt.decode(tokens.refresh_token, _TEST_SECRET, algorithms=["HS256"])
    assert refresh_payload["sub"] == str(user_id)
    assert refresh_payload["token_version"] == token_version
    assert refresh_payload["type"] == "refresh"
    assert "exp" in refresh_payload

    # Refresh expiry should be later than access expiry
    assert refresh_payload["exp"] > access_payload["exp"]


# ---------------------------------------------------------------------------
# 4. Token verification - valid
# ---------------------------------------------------------------------------


def test_verify_token_valid():
    """A properly signed, non-expired token should decode successfully."""
    user_id = uuid.uuid4()

    with patch.object(settings, "JWT_SECRET_KEY", _TEST_SECRET):
        tokens = create_tokens(user_id, token_version=0)
        payload = verify_token(tokens.access_token, "access")

    assert payload["sub"] == str(user_id)
    assert payload["type"] == "access"


# ---------------------------------------------------------------------------
# 5. Token verification - expired
# ---------------------------------------------------------------------------


def test_verify_token_expired():
    """An expired token should raise HTTPException(401)."""
    from fastapi import HTTPException

    payload = {
        "sub": str(uuid.uuid4()),
        "token_version": 0,
        "type": "access",
        "exp": datetime.now(timezone.utc) - timedelta(seconds=10),
        "iat": datetime.now(timezone.utc) - timedelta(minutes=20),
    }
    expired_token = jwt.encode(payload, _TEST_SECRET, algorithm="HS256")

    with patch.object(settings, "JWT_SECRET_KEY", _TEST_SECRET):
        with pytest.raises(HTTPException) as exc_info:
            verify_token(expired_token, "access")

    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# 6. Token verification - wrong type
# ---------------------------------------------------------------------------


def test_verify_token_wrong_type():
    """Using an access token where a refresh token is expected (and vice versa)
    should raise HTTPException(401)."""
    from fastapi import HTTPException

    user_id = uuid.uuid4()

    with patch.object(settings, "JWT_SECRET_KEY", _TEST_SECRET):
        tokens = create_tokens(user_id, token_version=0)

        # Access token used as refresh
        with pytest.raises(HTTPException) as exc_info:
            verify_token(tokens.access_token, "refresh")
        assert exc_info.value.status_code == 401

        # Refresh token used as access
        with pytest.raises(HTTPException) as exc_info:
            verify_token(tokens.refresh_token, "access")
        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# 7. Register endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_endpoint():
    """POST /api/v1/auth/register should return 201 with user data."""
    from app.main import app
    from app.database import get_db

    fake_user_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    # Build a mock DB session
    mock_db = AsyncMock()
    # First call: check email uniqueness -> None (not taken)
    # Second call: check username uniqueness -> None (not taken)
    mock_db.execute.side_effect = [
        _build_scalar_result(None),
        _build_scalar_result(None),
    ]

    # db.add() is synchronous on a real session, so use a plain MagicMock.
    # We intercept it to capture the User and simulate DB-assigned defaults.
    def capture_add(obj):
        obj.user_id = fake_user_id
        obj.phone_verified = False
        obj.credit_balance = 0
        obj.created_at = now
        obj.is_active = True
        obj.token_version = 0

    mock_db.add = MagicMock(side_effect=capture_add)
    mock_db.flush = AsyncMock()
    mock_db.refresh = AsyncMock()

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": "newuser@example.com",
                    "username": "newuser",
                    "password": "securepass123",
                },
            )

        assert response.status_code == 201, response.text
        data = response.json()
        assert data["email"] == "newuser@example.com"
        assert data["username"] == "newuser"
        assert data["user_id"] == str(fake_user_id)
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 8. Login endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_endpoint():
    """POST /api/v1/auth/login should return tokens and set cookies."""
    from app.main import app
    from app.database import get_db

    password = "securepassword123"
    fake_user = _make_fake_user(password=password)

    mock_db = AsyncMock()
    mock_db.execute.return_value = _build_scalar_result(fake_user)

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db

    try:
        with patch.object(settings, "JWT_SECRET_KEY", _TEST_SECRET):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/api/v1/auth/login",
                    json={
                        "email": fake_user.email,
                        "password": password,
                    },
                )

        assert response.status_code == 200, response.text
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"

        # Verify cookies were set
        cookies = response.headers.get_list("set-cookie")
        cookie_names = [c.split("=")[0] for c in cookies]
        assert "access_token" in cookie_names
        assert "refresh_token" in cookie_names
    finally:
        app.dependency_overrides.clear()
