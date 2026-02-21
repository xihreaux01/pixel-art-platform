"""Tests for the generation pipeline API endpoints.

All external dependencies (DB, Redis, Ollama) are mocked.

Run with:
    ./venv/bin/python -m pytest tests/test_generation_pipeline.py -v
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import get_current_user
from app.database import get_db
from app.main import app
from app.models import User


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_USER_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
FAKE_JOB_ID = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def _make_fake_user(user_id: uuid.UUID = FAKE_USER_ID) -> MagicMock:
    """Build a MagicMock that quacks like a User ORM instance."""
    user = MagicMock(spec=User)
    user.user_id = user_id
    user.email = "test@example.com"
    user.username = "testuser"
    user.is_active = True
    user.credit_balance = 100
    return user


def _row(*values):
    """Create a lightweight tuple-like object returned by fetchone/fetchall."""
    return values


def _make_mock_db():
    """Return an AsyncMock that behaves like an AsyncSession."""
    db = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


def _tier_cost_row(cost: int = 3):
    """Mock result for tier credit cost lookup."""
    result = MagicMock()
    result.fetchone.return_value = _row(cost)
    return result


def _deduct_result(new_balance: int = 97):
    """Mock result for atomic deduction UPDATE RETURNING."""
    result = MagicMock()
    result.fetchone.return_value = _row(new_balance)
    return result


def _insert_result():
    """Mock result for INSERT (no return value needed)."""
    return MagicMock()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_overrides():
    """Clear dependency overrides after every test."""
    yield
    app.dependency_overrides.clear()


def _override_auth(user: MagicMock | None = None):
    """Set up auth and DB dependency overrides. Returns the mock DB session."""
    fake_user = user or _make_fake_user()

    async def override_get_current_user():
        return fake_user

    app.dependency_overrides[get_current_user] = override_get_current_user
    return fake_user


def _override_db(mock_db: AsyncMock):
    """Override the get_db dependency with a mock session."""
    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db


# ---------------------------------------------------------------------------
# 1. test_create_generation_job
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_generation_job():
    """POST /api/v1/generations creates a job, deducts credits, returns 201."""
    fake_user = _override_auth()
    mock_db = _make_mock_db()

    # Execute calls: (1) tier lookup, (2) deduction UPDATE, (3) deduction INSERT txn, (4) job INSERT
    mock_db.execute.side_effect = [
        _tier_cost_row(3),
        _deduct_result(97),
        _insert_result(),
        _insert_result(),
    ]
    _override_db(mock_db)

    # Mock redis on app.state
    mock_redis = AsyncMock()
    app.state.redis = mock_redis

    with patch("app.api.generations.asyncio") as mock_asyncio:
        mock_asyncio.create_task = MagicMock()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/generations",
                json={"tier": "medium", "prompt": "a cute tree"},
            )

    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert "job_id" in data
    assert data["status"] == "pending"
    mock_db.commit.assert_called()


# ---------------------------------------------------------------------------
# 2. test_create_generation_insufficient_credits
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_generation_insufficient_credits():
    """POST /api/v1/generations returns 402 when user has insufficient credits."""
    _override_auth()
    mock_db = _make_mock_db()

    # (1) tier lookup succeeds, (2) deduction fails (returns None -> 402)
    deduct_fail = MagicMock()
    deduct_fail.fetchone.return_value = None

    mock_db.execute.side_effect = [
        _tier_cost_row(3),
        deduct_fail,
    ]
    _override_db(mock_db)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/generations",
            json={"tier": "medium", "prompt": "a cute tree"},
        )

    assert resp.status_code == 402
    assert "Insufficient credits" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 3. test_get_job_status
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_job_status():
    """GET /api/v1/generations/{job_id} returns current job status."""
    fake_user = _override_auth()
    mock_db = _make_mock_db()

    now = datetime.now(timezone.utc)
    job_row = _row(
        FAKE_JOB_ID, fake_user.user_id, "medium", "executing_tools",
        None, 42, None, now, None,
    )
    result = MagicMock()
    result.fetchone.return_value = job_row
    mock_db.execute.return_value = result
    _override_db(mock_db)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/v1/generations/{FAKE_JOB_ID}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["job_id"] == str(FAKE_JOB_ID)
    assert data["status"] == "executing_tools"
    assert data["tool_calls_executed"] == 42
    assert data["art_id"] is None


# ---------------------------------------------------------------------------
# 4. test_cancel_pending_job
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_pending_job():
    """DELETE /api/v1/generations/{job_id} cancels a pending job and refunds."""
    fake_user = _override_auth()
    mock_db = _make_mock_db()

    now = datetime.now(timezone.utc)
    job_row = _row(
        FAKE_JOB_ID, fake_user.user_id, "medium", "pending",
        None, 0, None, now, None,
    )
    job_result = MagicMock()
    job_result.fetchone.return_value = job_row

    # Calls: (1) load job, (2) tier cost, (3) UPDATE status, (4) refund UPDATE, (5) refund INSERT txn
    mock_db.execute.side_effect = [
        job_result,
        _tier_cost_row(3),
        _insert_result(),
        _deduct_result(103),
        _insert_result(),
    ]
    _override_db(mock_db)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.delete(f"/api/v1/generations/{FAKE_JOB_ID}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "cancelled"
    mock_db.commit.assert_called()


# ---------------------------------------------------------------------------
# 5. test_cancel_completed_job_fails
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_completed_job_fails():
    """DELETE on a completed job returns 409 Conflict."""
    fake_user = _override_auth()
    mock_db = _make_mock_db()

    now = datetime.now(timezone.utc)
    art_id = uuid.uuid4()
    job_row = _row(
        FAKE_JOB_ID, fake_user.user_id, "medium", "completed",
        art_id, 100, None, now, now,
    )
    job_result = MagicMock()
    job_result.fetchone.return_value = job_row
    mock_db.execute.return_value = job_result
    _override_db(mock_db)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.delete(f"/api/v1/generations/{FAKE_JOB_ID}")

    assert resp.status_code == 409
    assert "Cannot cancel" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 6. test_sse_events
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sse_events():
    """GET /api/v1/generations/{job_id}/events returns an SSE stream."""
    fake_user = _override_auth()
    mock_db = _make_mock_db()

    # Ownership check
    owner_result = MagicMock()
    owner_result.fetchone.return_value = _row(fake_user.user_id)
    mock_db.execute.return_value = owner_result
    _override_db(mock_db)

    # Build mock Redis pub/sub
    progress_event = json.dumps({
        "event": "progress", "tool_calls_executed": 5,
        "tool_budget": 100, "status": "executing_tools",
    })
    complete_event = json.dumps({"event": "complete", "art_id": str(uuid.uuid4())})

    mock_pubsub = MagicMock()
    call_count = 0

    async def mock_get_message(ignore_subscribe_messages=True, timeout=1.0):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {"type": "message", "data": progress_event}
        if call_count == 2:
            return {"type": "message", "data": complete_event}
        return None

    mock_pubsub.get_message = mock_get_message
    mock_pubsub.subscribe = AsyncMock()
    mock_pubsub.unsubscribe = AsyncMock()
    mock_pubsub.close = AsyncMock()

    mock_redis = MagicMock()
    mock_redis.pubsub.return_value = mock_pubsub
    app.state.redis = mock_redis

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/v1/generations/{FAKE_JOB_ID}/events")

    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]

    body = resp.text
    assert "data:" in body
    assert "progress" in body
    assert "complete" in body
