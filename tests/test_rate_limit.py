"""Tests for rate limiting and security headers middleware."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


def _set_mock_redis(mock_redis):
    """Assign a mock Redis instance to app.state and return the previous value."""
    previous = getattr(app.state, "redis", None)
    app.state.redis = mock_redis
    return previous


def _restore_redis(previous):
    """Restore app.state.redis to its previous value."""
    if previous is None:
        try:
            del app.state.redis
        except AttributeError:
            pass
    else:
        app.state.redis = previous


@pytest.mark.asyncio
async def test_security_headers():
    """Verify security headers are present on the /health response."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-XSS-Protection"] == "1; mode=block"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "camera=()" in response.headers["Permissions-Policy"]


@pytest.mark.asyncio
async def test_health_not_rate_limited():
    """/health endpoint should never receive rate limit headers."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert "X-RateLimit-Limit" not in response.headers
    assert "X-RateLimit-Remaining" not in response.headers
    assert "X-RateLimit-Reset" not in response.headers


@pytest.mark.asyncio
async def test_rate_limit_headers():
    """Verify X-RateLimit-* headers are returned for rate-limited endpoints."""
    mock_pipe = MagicMock()
    mock_pipe.zremrangebyscore = MagicMock(return_value=mock_pipe)
    mock_pipe.zadd = MagicMock(return_value=mock_pipe)
    mock_pipe.zcard = MagicMock(return_value=mock_pipe)
    mock_pipe.expire = MagicMock(return_value=mock_pipe)
    # Pipeline results: [zremrangebyscore, zadd, zcard, expire]
    mock_pipe.execute = AsyncMock(return_value=[0, True, 1, True])

    mock_redis = AsyncMock()
    mock_redis.pipeline = MagicMock(return_value=mock_pipe)

    @app.get("/api/v1/ratelimit-probe")
    async def _ratelimit_stub():
        return {"ok": True}

    from app.middleware.rate_limit import RATE_LIMIT_RULES

    original_rules = RATE_LIMIT_RULES.copy()
    RATE_LIMIT_RULES.append({
        "path": "/api/v1/ratelimit-probe",
        "limit": 100,
        "window": 60,
        "key": "ip",
    })

    previous = _set_mock_redis(mock_redis)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/ratelimit-probe")

        assert response.status_code == 200
        assert "X-RateLimit-Limit" in response.headers
        assert "X-RateLimit-Remaining" in response.headers
        assert "X-RateLimit-Reset" in response.headers
        assert response.headers["X-RateLimit-Limit"] == "100"
    finally:
        _restore_redis(previous)
        RATE_LIMIT_RULES.clear()
        RATE_LIMIT_RULES.extend(original_rules)


@pytest.mark.asyncio
async def test_rate_limit_skipped_on_redis_error():
    """When Redis is unavailable the request should still succeed (fail-open)."""
    mock_redis = AsyncMock()
    mock_redis.pipeline = MagicMock(side_effect=ConnectionError("Redis down"))

    @app.post("/api/v1/auth/register_test_fallback")
    async def _register_stub():
        return {"ok": True}

    from app.middleware.rate_limit import RATE_LIMIT_RULES

    original_rules = RATE_LIMIT_RULES.copy()
    RATE_LIMIT_RULES.append({
        "path": "/api/v1/auth/register_test_fallback",
        "limit": 1,
        "window": 60,
        "key": "ip",
    })

    previous = _set_mock_redis(mock_redis)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/v1/auth/register_test_fallback")

        assert response.status_code == 200
        assert "X-RateLimit-Limit" not in response.headers
    finally:
        _restore_redis(previous)
        RATE_LIMIT_RULES.clear()
        RATE_LIMIT_RULES.extend(original_rules)
