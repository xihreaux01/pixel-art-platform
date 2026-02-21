"""Tests for credit service functions and the credits API endpoint."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.services.credit_service import (
    add_credits,
    atomic_deduct_credits,
    get_available_packs,
    refund_credits,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def _make_mock_db():
    """Return an AsyncMock that behaves like an AsyncSession."""
    return AsyncMock()


def _row(*values):
    """Create a lightweight tuple-like object returned by fetchone/fetchall."""
    return values


# ---------------------------------------------------------------------------
# test_get_available_packs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_available_packs():
    """get_available_packs should return a list of CreditPackResponse."""
    mock_db = _make_mock_db()

    fake_rows = [
        _row(1, "Starter", 499, 50),
        _row(2, "Pro", 1999, 250),
    ]
    result_proxy = MagicMock()
    result_proxy.fetchall.return_value = fake_rows
    mock_db.execute.return_value = result_proxy

    packs = await get_available_packs(mock_db)

    assert len(packs) == 2
    assert packs[0].pack_id == 1
    assert packs[0].name == "Starter"
    assert packs[0].price_cents == 499
    assert packs[0].credit_amount == 50
    assert packs[1].pack_id == 2
    assert packs[1].name == "Pro"


# ---------------------------------------------------------------------------
# test_atomic_deduct_success
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_atomic_deduct_success():
    """atomic_deduct_credits succeeds when balance >= cost."""
    mock_db = _make_mock_db()

    # First execute call: the UPDATE RETURNING
    update_result = MagicMock()
    update_result.fetchone.return_value = _row(80)  # new balance

    # Second execute call: the INSERT into credit_transactions
    insert_result = MagicMock()

    mock_db.execute.side_effect = [update_result, insert_result]

    new_balance = await atomic_deduct_credits(
        db=mock_db,
        user_id=FAKE_USER_ID,
        amount=20,
        txn_type="spend",
        reference_id=uuid.uuid4(),
    )

    assert new_balance == 80
    assert mock_db.execute.call_count == 2


# ---------------------------------------------------------------------------
# test_atomic_deduct_insufficient
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_atomic_deduct_insufficient():
    """atomic_deduct_credits raises 402 when balance < cost."""
    mock_db = _make_mock_db()

    update_result = MagicMock()
    update_result.fetchone.return_value = None  # no row -> insufficient

    mock_db.execute.return_value = update_result

    with pytest.raises(HTTPException) as exc_info:
        await atomic_deduct_credits(
            db=mock_db,
            user_id=FAKE_USER_ID,
            amount=9999,
            txn_type="spend",
        )

    assert exc_info.value.status_code == 402
    assert "Insufficient credits" in exc_info.value.detail


# ---------------------------------------------------------------------------
# test_add_credits
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_credits():
    """add_credits should increase balance and return the new value."""
    mock_db = _make_mock_db()

    update_result = MagicMock()
    update_result.fetchone.return_value = _row(150)  # new balance after add

    insert_result = MagicMock()

    mock_db.execute.side_effect = [update_result, insert_result]

    new_balance = await add_credits(
        db=mock_db,
        user_id=FAKE_USER_ID,
        amount=50,
        txn_type="purchase",
        reference_id=uuid.uuid4(),
    )

    assert new_balance == 150
    assert mock_db.execute.call_count == 2


# ---------------------------------------------------------------------------
# test_refund_credits
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_refund_credits():
    """refund_credits delegates to add_credits with txn_type='refund'."""
    mock_db = _make_mock_db()

    update_result = MagicMock()
    update_result.fetchone.return_value = _row(200)

    insert_result = MagicMock()

    mock_db.execute.side_effect = [update_result, insert_result]

    ref_id = uuid.uuid4()
    new_balance = await refund_credits(
        db=mock_db,
        user_id=FAKE_USER_ID,
        amount=25,
        reference_id=ref_id,
    )

    assert new_balance == 200

    # Verify the INSERT used txn_type='refund' -- check second execute call
    insert_call_args = mock_db.execute.call_args_list[1]
    params = insert_call_args[0][1]  # positional arg [1] is the params dict
    assert params["txn_type"] == "refund"
    assert params["amount"] == 25


# ---------------------------------------------------------------------------
# test_credit_packs_endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_credit_packs_endpoint():
    """GET /api/v1/credits/packs returns the available packs."""
    from app.main import app

    fake_rows = [
        _row(1, "Starter", 499, 50),
        _row(2, "Pro", 1999, 250),
    ]

    mock_result = MagicMock()
    mock_result.fetchall.return_value = fake_rows

    mock_session = AsyncMock()
    mock_session.execute.return_value = mock_result
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()

    async def _override_get_db():
        yield mock_session

    from app.database import get_db
    app.dependency_overrides[get_db] = _override_get_db

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/credits/packs")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["pack_id"] == 1
        assert data[0]["name"] == "Starter"
        assert data[1]["credit_amount"] == 250
    finally:
        app.dependency_overrides.clear()
