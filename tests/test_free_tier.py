"""Tests for the free tier service.

Validates:
1. Free generation succeeds for phone-verified users with 0 gens today
2. Free generation rejected for non-phone-verified users (403)
3. Free generation rejected when daily limit already used (429)
4. Free generation counter resets on a new day
5. Free-tier art pieces are non-tradeable (is_tradeable = false)

Run with:
    ./venv/bin/python -m pytest tests/test_free_tier.py -v
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.services.free_tier_service import (
    check_free_tier_eligibility,
    record_free_generation,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000099")


def _make_mock_db():
    """Return an AsyncMock that behaves like an AsyncSession."""
    return AsyncMock()


def _row(*values):
    """Create a lightweight tuple-like object returned by fetchone."""
    return values


# ---------------------------------------------------------------------------
# 1. test_free_gen_succeeds_phone_verified
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_free_gen_succeeds_phone_verified():
    """Phone-verified user with 0 free gens today should be eligible."""
    mock_db = _make_mock_db()

    # phone_verified=True, free_generations_today=0, last_free_gen_date=today
    result_proxy = MagicMock()
    result_proxy.fetchone.return_value = _row(True, 0, date.today())
    mock_db.execute.return_value = result_proxy

    result = await check_free_tier_eligibility(mock_db, FAKE_USER_ID)

    assert result["eligible"] is True
    assert result["free_generations_today"] == 0
    assert mock_db.execute.call_count == 1


# ---------------------------------------------------------------------------
# 2. test_free_gen_rejected_not_phone_verified
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_free_gen_rejected_not_phone_verified():
    """Non-phone-verified user should be rejected with 403."""
    mock_db = _make_mock_db()

    # phone_verified=False
    result_proxy = MagicMock()
    result_proxy.fetchone.return_value = _row(False, 0, None)
    mock_db.execute.return_value = result_proxy

    with pytest.raises(HTTPException) as exc_info:
        await check_free_tier_eligibility(mock_db, FAKE_USER_ID)

    assert exc_info.value.status_code == 403
    assert "Phone verification required" in exc_info.value.detail


# ---------------------------------------------------------------------------
# 3. test_free_gen_rejected_already_used_today
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_free_gen_rejected_already_used_today():
    """User who already used free gen today should be rejected with 429."""
    mock_db = _make_mock_db()

    # phone_verified=True, free_generations_today=1, last_free_gen_date=today
    result_proxy = MagicMock()
    result_proxy.fetchone.return_value = _row(True, 1, date.today())
    mock_db.execute.return_value = result_proxy

    with pytest.raises(HTTPException) as exc_info:
        await check_free_tier_eligibility(mock_db, FAKE_USER_ID)

    assert exc_info.value.status_code == 429
    assert "Free generation limit reached" in exc_info.value.detail


# ---------------------------------------------------------------------------
# 4. test_free_gen_resets_on_new_day
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_free_gen_resets_on_new_day():
    """Counter should reset when last_free_gen_date is yesterday."""
    mock_db = _make_mock_db()

    yesterday = date.today() - timedelta(days=1)
    # phone_verified=True, free_generations_today=1, last_free_gen_date=yesterday
    result_proxy = MagicMock()
    result_proxy.fetchone.return_value = _row(True, 1, yesterday)
    mock_db.execute.return_value = result_proxy

    result = await check_free_tier_eligibility(mock_db, FAKE_USER_ID)

    assert result["eligible"] is True
    # Counter should have been logically reset to 0
    assert result["free_generations_today"] == 0


# ---------------------------------------------------------------------------
# 5. test_free_art_non_tradeable
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_free_art_non_tradeable():
    """Free-tier art piece should have is_tradeable = false.

    Verifies the orchestrator's save_art_piece passes tradeable=False
    when tier_name is 'free'.
    """
    from app.services.generation_orchestrator import JobContext, TierConfig

    tier = TierConfig(
        canvas_width=16,
        canvas_height=16,
        credit_cost=0,
        tool_budget_soft=80,
        tool_budget_hard=100,
        job_timeout_seconds=300,
        allowed_tools=["set_pixel", "seal_canvas"],
    )

    ctx = JobContext(
        job_id=uuid.uuid4(),
        user_id=FAKE_USER_ID,
        tier_name="free",
        tier=tier,
    )

    mock_db = _make_mock_db()
    mock_db.execute.return_value = MagicMock()

    from app.services.generation_orchestrator import JobRepository

    repo = JobRepository(mock_db)
    await repo.save_art_piece(
        ctx,
        seal_sig="fakesig",
        gen_hash="fakehash",
        paths=("/var/art/test.png", "/var/art/test_thumb.png"),
    )

    # Inspect the params dict passed to db.execute
    call_args = mock_db.execute.call_args
    params = call_args[0][1]  # positional arg [1] is the params dict
    assert params["tradeable"] is False

    # Also verify a paid tier would be tradeable
    ctx_paid = JobContext(
        job_id=uuid.uuid4(),
        user_id=FAKE_USER_ID,
        tier_name="basic",
        tier=tier,
    )

    mock_db_paid = _make_mock_db()
    mock_db_paid.execute.return_value = MagicMock()

    repo_paid = JobRepository(mock_db_paid)
    await repo_paid.save_art_piece(
        ctx_paid,
        seal_sig="fakesig",
        gen_hash="fakehash",
        paths=("/var/art/test.png", "/var/art/test_thumb.png"),
    )

    paid_params = mock_db_paid.execute.call_args[0][1]
    assert paid_params["tradeable"] is True
