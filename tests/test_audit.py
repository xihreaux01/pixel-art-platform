"""Tests for audit logging and the provenance API endpoint.

All database interactions are mocked -- no real DB required.

Run with:
    ./venv/bin/python -m pytest tests/test_audit.py -v
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.services.audit_logger import AuditLogger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_USER_A = uuid.UUID("00000000-0000-0000-0000-000000000001")
FAKE_USER_B = uuid.UUID("00000000-0000-0000-0000-000000000002")
FAKE_ART_ID = uuid.UUID("00000000-0000-0000-0000-000000000077")
FAKE_TXN_ID = uuid.UUID("00000000-0000-0000-0000-000000000099")


def _row(*values):
    """Create a lightweight tuple-like object returned by fetchone/fetchall."""
    return values


def _make_fake_user():
    """Return a MagicMock that satisfies get_current_user."""
    user = MagicMock()
    user.user_id = FAKE_USER_A
    user.is_active = True
    user.token_version = 1
    return user


# ---------------------------------------------------------------------------
# 1. test_log_trade_structured
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_log_trade_structured():
    """Verify trade logging includes all fields and the audit flag."""
    logger = AuditLogger()

    trade_details = {
        "buyer_user_id": FAKE_USER_A,
        "seller_user_id": FAKE_USER_B,
        "art_id": FAKE_ART_ID,
        "amount": 500,
        "fees": 50,
        "transaction_id": FAKE_TXN_ID,
    }

    with patch("app.services.audit_logger.log") as mock_log:
        logger.log_trade(trade_details)

        mock_log.info.assert_called_once()
        call_kwargs = mock_log.info.call_args[1]

        assert call_kwargs["event_type"] == "trade"
        assert call_kwargs["audit"] is True
        assert call_kwargs["buyer_user_id"] == str(FAKE_USER_A)
        assert call_kwargs["seller_user_id"] == str(FAKE_USER_B)
        assert call_kwargs["art_id"] == str(FAKE_ART_ID)
        assert call_kwargs["amount"] == 500
        assert call_kwargs["fees"] == 50
        assert call_kwargs["transaction_id"] == str(FAKE_TXN_ID)
        assert "timestamp" in call_kwargs


# ---------------------------------------------------------------------------
# 2. test_log_ownership_transfer
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_log_ownership_transfer():
    """Verify ownership transfer log structure."""
    logger = AuditLogger()

    with patch("app.services.audit_logger.log") as mock_log:
        logger.log_ownership_transfer(
            art_id=FAKE_ART_ID,
            from_user_id=FAKE_USER_A,
            to_user_id=FAKE_USER_B,
            transfer_type="trade",
            transaction_id=FAKE_TXN_ID,
        )

        mock_log.info.assert_called_once()
        call_kwargs = mock_log.info.call_args[1]

        assert call_kwargs["event_type"] == "ownership_transfer"
        assert call_kwargs["audit"] is True
        assert call_kwargs["art_id"] == str(FAKE_ART_ID)
        assert call_kwargs["from_user_id"] == str(FAKE_USER_A)
        assert call_kwargs["to_user_id"] == str(FAKE_USER_B)
        assert call_kwargs["transfer_type"] == "trade"
        assert call_kwargs["transaction_id"] == str(FAKE_TXN_ID)
        assert "timestamp" in call_kwargs


# ---------------------------------------------------------------------------
# 3. test_provenance_chain_creation_only
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_provenance_chain_creation_only():
    """Art with no trades returns a single creation entry."""
    from app.main import app
    from app.api.dependencies import get_current_user
    from app.database import get_db

    created_at = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    transferred_at = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)

    # Art piece row
    art_row = _row(FAKE_ART_ID, FAKE_USER_A, "medium", created_at, "abcdef01")

    # Single creation entry in ownership_history
    history_rows = [
        _row(None, FAKE_USER_A, "creation", None, transferred_at),
    ]

    mock_session = AsyncMock()
    art_result = MagicMock()
    art_result.fetchone.return_value = art_row
    history_result = MagicMock()
    history_result.fetchall.return_value = history_rows
    mock_session.execute.side_effect = [art_result, history_result]
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()

    async def _override_get_db():
        yield mock_session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = lambda: _make_fake_user()

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/v1/art/{FAKE_ART_ID}/provenance")

        assert resp.status_code == 200
        data = resp.json()
        assert data["art_id"] == str(FAKE_ART_ID)
        assert data["creator_user_id"] == str(FAKE_USER_A)
        assert data["generation_tier"] == "medium"
        assert data["seal_signature"] == "abcdef01"
        assert len(data["provenance_chain"]) == 1
        assert data["provenance_chain"][0]["transfer_type"] == "creation"
        assert data["provenance_chain"][0]["from_user_id"] is None
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 4. test_provenance_chain_with_trades
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_provenance_chain_with_trades():
    """Art with trades returns the full chain in chronological order."""
    from app.main import app
    from app.api.dependencies import get_current_user
    from app.database import get_db

    created_at = datetime(2025, 1, 10, 8, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2025, 1, 10, 8, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2025, 2, 5, 14, 30, 0, tzinfo=timezone.utc)

    art_row = _row(FAKE_ART_ID, FAKE_USER_A, "high", created_at, "deadbeef")

    history_rows = [
        _row(None, FAKE_USER_A, "creation", None, t1),
        _row(FAKE_USER_A, FAKE_USER_B, "trade", FAKE_TXN_ID, t2),
    ]

    mock_session = AsyncMock()
    art_result = MagicMock()
    art_result.fetchone.return_value = art_row
    history_result = MagicMock()
    history_result.fetchall.return_value = history_rows
    mock_session.execute.side_effect = [art_result, history_result]
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()

    async def _override_get_db():
        yield mock_session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = lambda: _make_fake_user()

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/v1/art/{FAKE_ART_ID}/provenance")

        assert resp.status_code == 200
        data = resp.json()
        chain = data["provenance_chain"]
        assert len(chain) == 2
        assert chain[0]["transfer_type"] == "creation"
        assert chain[0]["from_user_id"] is None
        assert chain[1]["transfer_type"] == "trade"
        assert chain[1]["from_user_id"] == str(FAKE_USER_A)
        assert chain[1]["to_user_id"] == str(FAKE_USER_B)
        assert chain[1]["transaction_id"] == str(FAKE_TXN_ID)
        # Chronological: creation before trade
        assert chain[0]["transferred_at"] <= chain[1]["transferred_at"]
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 5. test_provenance_art_not_found
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_provenance_art_not_found():
    """Returns 404 for a nonexistent art piece."""
    from app.main import app
    from app.api.dependencies import get_current_user
    from app.database import get_db

    mock_session = AsyncMock()
    art_result = MagicMock()
    art_result.fetchone.return_value = None  # art not found
    mock_session.execute.return_value = art_result
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()

    async def _override_get_db():
        yield mock_session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = lambda: _make_fake_user()

    nonexistent_id = uuid.UUID("99999999-9999-9999-9999-999999999999")

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/v1/art/{nonexistent_id}/provenance")

        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 6. test_log_credit_event
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_log_credit_event():
    """Verify credit event log includes user, amount, type, and audit flag."""
    logger = AuditLogger()

    with patch("app.services.audit_logger.log") as mock_log:
        logger.log_credit_event(
            user_id=FAKE_USER_A,
            amount=100,
            txn_type="purchase",
            reference_id=FAKE_TXN_ID,
        )

        mock_log.info.assert_called_once()
        call_kwargs = mock_log.info.call_args[1]

        assert call_kwargs["event_type"] == "credit_event"
        assert call_kwargs["audit"] is True
        assert call_kwargs["user_id"] == str(FAKE_USER_A)
        assert call_kwargs["amount"] == 100
        assert call_kwargs["txn_type"] == "purchase"
        assert call_kwargs["reference_id"] == str(FAKE_TXN_ID)


# ---------------------------------------------------------------------------
# 7. test_log_moderation_event
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_log_moderation_event():
    """Verify moderation event log includes violation and action details."""
    logger = AuditLogger()

    with patch("app.services.audit_logger.log") as mock_log:
        logger.log_moderation_event(
            user_id=FAKE_USER_A,
            art_id=FAKE_ART_ID,
            violation_type="nsfw",
            action_taken="quarantine",
        )

        mock_log.info.assert_called_once()
        call_kwargs = mock_log.info.call_args[1]

        assert call_kwargs["event_type"] == "moderation"
        assert call_kwargs["audit"] is True
        assert call_kwargs["user_id"] == str(FAKE_USER_A)
        assert call_kwargs["art_id"] == str(FAKE_ART_ID)
        assert call_kwargs["violation_type"] == "nsfw"
        assert call_kwargs["action_taken"] == "quarantine"
