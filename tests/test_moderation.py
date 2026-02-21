"""Tests for the content moderation service.

All database interactions are mocked -- no real DB required.

Run with:
    ./venv/bin/python -m pytest tests/test_moderation.py -v
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from app.services.content_moderator import ContentModerator, ModerationResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
FAKE_JOB_ID = uuid.UUID("00000000-0000-0000-0000-000000000099")
FAKE_ART_ID = uuid.UUID("00000000-0000-0000-0000-000000000077")


def _make_mock_db():
    """Return an AsyncMock that behaves like an AsyncSession."""
    return AsyncMock()


def _row(*values):
    """Create a lightweight tuple-like object returned by fetchone/fetchall."""
    return values


def _make_test_image(width: int = 32, height: int = 32) -> Image.Image:
    """Create a small test image."""
    return Image.new("RGB", (width, height), (128, 128, 128))


# ---------------------------------------------------------------------------
# 1. test_scan_stub_approves
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scan_stub_approves():
    """Default stub scanner always returns is_approved=True."""
    moderator = ContentModerator()
    img = _make_test_image()

    result = await moderator.scan(img)

    assert isinstance(result, ModerationResult)
    assert result.is_approved is True
    assert result.violation_type is None
    assert result.confidence == 0.0


# ---------------------------------------------------------------------------
# 2. test_record_violation_increments_count
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_record_violation_increments_count():
    """Recording a violation returns the updated violation count."""
    mock_db = _make_mock_db()
    moderator = ContentModerator()

    # Simulate the UPDATE RETURNING giving back count = 1
    update_result = MagicMock()
    update_result.fetchone.return_value = _row(1)
    mock_db.execute.return_value = update_result

    count = await moderator.record_violation(
        db=mock_db,
        user_id=FAKE_USER_ID,
        art_id=FAKE_ART_ID,
        violation_type="nsfw",
    )

    assert count == 1
    # Only one execute call: the UPDATE (no suspension since count < 3)
    assert mock_db.execute.call_count == 1


# ---------------------------------------------------------------------------
# 3. test_third_violation_suspends_account
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_third_violation_suspends_account():
    """Third violation sets is_active=False (account suspension)."""
    mock_db = _make_mock_db()
    moderator = ContentModerator()

    # The UPDATE RETURNING returns count = 3 (the threshold)
    update_result = MagicMock()
    update_result.fetchone.return_value = _row(3)

    # The second execute call is the suspension UPDATE
    suspend_result = MagicMock()

    mock_db.execute.side_effect = [update_result, suspend_result]

    count = await moderator.record_violation(
        db=mock_db,
        user_id=FAKE_USER_ID,
        art_id=FAKE_ART_ID,
        violation_type="hate_symbol",
    )

    assert count == 3
    # Two execute calls: increment + suspension
    assert mock_db.execute.call_count == 2

    # Verify the second call sets is_active = FALSE
    suspend_call = mock_db.execute.call_args_list[1]
    sql_text = str(suspend_call[0][0].text)
    assert "is_active" in sql_text
    assert "FALSE" in sql_text


# ---------------------------------------------------------------------------
# 4. test_handle_violation_refunds_credits
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_violation_refunds_credits():
    """handle_violation calls refund_credits with the correct amount."""
    mock_db = _make_mock_db()
    moderator = ContentModerator()

    # We need to mock refund_credits and record_violation to isolate
    with patch(
        "app.services.content_moderator.refund_credits", new_callable=AsyncMock
    ) as mock_refund:
        mock_refund.return_value = 100  # new balance after refund

        # Mock record_violation on the instance
        moderator.record_violation = AsyncMock(return_value=1)

        # The first execute is the quarantine UPDATE (job -> failed)
        quarantine_result = MagicMock()
        mock_db.execute.return_value = quarantine_result

        await moderator.handle_violation(
            db=mock_db,
            user_id=FAKE_USER_ID,
            job_id=FAKE_JOB_ID,
            violation_type="violence",
            credit_cost=25,
        )

        # Verify refund_credits was called with the right args
        mock_refund.assert_awaited_once_with(
            db=mock_db,
            user_id=FAKE_USER_ID,
            amount=25,
            reference_id=FAKE_JOB_ID,
        )


# ---------------------------------------------------------------------------
# 5. test_handle_violation_records
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_violation_records():
    """handle_violation calls record_violation with violation details."""
    mock_db = _make_mock_db()
    moderator = ContentModerator()

    with patch(
        "app.services.content_moderator.refund_credits", new_callable=AsyncMock
    ) as mock_refund:
        mock_refund.return_value = 50

        # Mock record_violation on the instance
        moderator.record_violation = AsyncMock(return_value=2)

        quarantine_result = MagicMock()
        mock_db.execute.return_value = quarantine_result

        await moderator.handle_violation(
            db=mock_db,
            user_id=FAKE_USER_ID,
            job_id=FAKE_JOB_ID,
            violation_type="nsfw",
            credit_cost=10,
        )

        # Verify record_violation was called
        moderator.record_violation.assert_awaited_once_with(
            db=mock_db,
            user_id=FAKE_USER_ID,
            art_id=FAKE_JOB_ID,
            violation_type="nsfw",
        )


# ---------------------------------------------------------------------------
# 6. test_handle_violation_quarantines_job
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_violation_quarantines_job():
    """handle_violation marks the generation job as 'failed'."""
    mock_db = _make_mock_db()
    moderator = ContentModerator()

    with patch(
        "app.services.content_moderator.refund_credits", new_callable=AsyncMock
    ) as mock_refund:
        mock_refund.return_value = 50

        moderator.record_violation = AsyncMock(return_value=1)

        quarantine_result = MagicMock()
        mock_db.execute.return_value = quarantine_result

        await moderator.handle_violation(
            db=mock_db,
            user_id=FAKE_USER_ID,
            job_id=FAKE_JOB_ID,
            violation_type="violence",
            credit_cost=15,
        )

        # The first db.execute call should be the quarantine UPDATE
        first_call = mock_db.execute.call_args_list[0]
        sql_text = str(first_call[0][0].text)
        assert "art_generation_jobs" in sql_text
        assert "failed" in sql_text

        params = first_call[0][1]
        assert params["job_id"] == FAKE_JOB_ID
        assert "violence" in params["error_msg"]


# ---------------------------------------------------------------------------
# 7. test_record_violation_user_not_found
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_record_violation_user_not_found():
    """record_violation raises ValueError when the user does not exist."""
    mock_db = _make_mock_db()
    moderator = ContentModerator()

    update_result = MagicMock()
    update_result.fetchone.return_value = None  # no row matched
    mock_db.execute.return_value = update_result

    with pytest.raises(ValueError, match="not found"):
        await moderator.record_violation(
            db=mock_db,
            user_id=FAKE_USER_ID,
            art_id=FAKE_ART_ID,
            violation_type="nsfw",
        )


# ---------------------------------------------------------------------------
# 8. test_second_violation_does_not_suspend
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_second_violation_does_not_suspend():
    """Two violations should NOT trigger suspension (threshold is 3)."""
    mock_db = _make_mock_db()
    moderator = ContentModerator()

    update_result = MagicMock()
    update_result.fetchone.return_value = _row(2)
    mock_db.execute.return_value = update_result

    count = await moderator.record_violation(
        db=mock_db,
        user_id=FAKE_USER_ID,
        art_id=FAKE_ART_ID,
        violation_type="violence",
    )

    assert count == 2
    # Only 1 execute call (no suspension UPDATE)
    assert mock_db.execute.call_count == 1
