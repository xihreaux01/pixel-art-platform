"""Tests for trade execution in the marketplace service."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.services.marketplace_service import execute_trade


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BUYER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
SELLER_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
ART_ID = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
LISTING_ID = uuid.UUID("00000000-0000-0000-0000-0000000000b1")


def _mock_db():
    """Return an AsyncMock that behaves like an AsyncSession."""
    return AsyncMock()


def _row(*values):
    """Create a lightweight tuple-like object returned by fetchone."""
    return values


def _listing_row(status: str = "active", price: int = 1000):
    """Build a marketplace_listings SELECT FOR UPDATE result row."""
    return _row(LISTING_ID, ART_ID, SELLER_ID, price, status)


def _art_row():
    """Build an art_pieces SELECT FOR UPDATE result row."""
    return _row(ART_ID, SELLER_ID)


# ---------------------------------------------------------------------------
# test_trade_success
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_trade_success():
    """Buyer purchases a listing; verify all DB calls are made."""
    db = _mock_db()

    # 1. Lock listing SELECT FOR UPDATE
    listing_result = MagicMock()
    listing_result.fetchone.return_value = _listing_row()

    # 2. Lock art piece SELECT FOR UPDATE
    art_result = MagicMock()
    art_result.fetchone.return_value = _art_row()

    # 3-4. atomic_deduct_credits: UPDATE RETURNING + INSERT credit_txn
    deduct_update = MagicMock()
    deduct_update.fetchone.return_value = _row(500)
    deduct_insert = MagicMock()

    # 5-6. add_credits: UPDATE RETURNING + INSERT credit_txn
    credit_update = MagicMock()
    credit_update.fetchone.return_value = _row(900)
    credit_insert = MagicMock()

    # 7. UPDATE art_pieces SET current_owner_id
    transfer_update = MagicMock()

    # 8. INSERT ownership_history
    ownership_insert = MagicMock()

    # 9. UPDATE marketplace_listings SET status='sold'
    listing_sold = MagicMock()

    # 10. INSERT transactions
    txn_insert = MagicMock()

    db.execute.side_effect = [
        listing_result,   # lock listing
        art_result,       # lock art
        deduct_update,    # deduct credits UPDATE
        deduct_insert,    # deduct credits INSERT txn
        credit_update,    # add credits UPDATE
        credit_insert,    # add credits INSERT txn
        transfer_update,  # transfer ownership UPDATE
        ownership_insert, # ownership history INSERT
        listing_sold,     # mark listing sold
        txn_insert,       # insert transaction record
    ]

    txn_id = await execute_trade(db, BUYER_ID, LISTING_ID)

    assert isinstance(txn_id, uuid.UUID)
    assert db.execute.call_count == 10


# ---------------------------------------------------------------------------
# test_trade_listing_not_active
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_trade_listing_not_active():
    """Listing already sold returns 409."""
    db = _mock_db()

    listing_result = MagicMock()
    listing_result.fetchone.return_value = _listing_row(status="sold")
    db.execute.return_value = listing_result

    with pytest.raises(HTTPException) as exc_info:
        await execute_trade(db, BUYER_ID, LISTING_ID)

    assert exc_info.value.status_code == 409
    assert "not active" in exc_info.value.detail


# ---------------------------------------------------------------------------
# test_trade_self_purchase_rejected
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_trade_self_purchase_rejected():
    """Seller trying to buy own listing returns 400."""
    db = _mock_db()

    listing_result = MagicMock()
    listing_result.fetchone.return_value = _listing_row()
    db.execute.return_value = listing_result

    with pytest.raises(HTTPException) as exc_info:
        await execute_trade(db, SELLER_ID, LISTING_ID)

    assert exc_info.value.status_code == 400
    assert "Cannot buy your own art" in exc_info.value.detail


# ---------------------------------------------------------------------------
# test_trade_insufficient_credits
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_trade_insufficient_credits():
    """Buyer with insufficient credits gets 402."""
    db = _mock_db()

    # Lock listing
    listing_result = MagicMock()
    listing_result.fetchone.return_value = _listing_row()

    # Lock art
    art_result = MagicMock()
    art_result.fetchone.return_value = _art_row()

    # atomic_deduct_credits UPDATE returns None -> insufficient
    deduct_result = MagicMock()
    deduct_result.fetchone.return_value = None

    db.execute.side_effect = [listing_result, art_result, deduct_result]

    with pytest.raises(HTTPException) as exc_info:
        await execute_trade(db, BUYER_ID, LISTING_ID)

    assert exc_info.value.status_code == 402
    assert "Insufficient credits" in exc_info.value.detail


# ---------------------------------------------------------------------------
# test_trade_fee_calculation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "price, expected_fee, expected_payout",
    [
        (1000, 100, 900),
        (999, 99, 900),
        (10, 1, 9),
        (1, 0, 1),
        (5555, 555, 5000),
    ],
)
async def test_trade_fee_calculation(price, expected_fee, expected_payout):
    """Verify 10% platform fee (integer division) across various amounts."""
    db = _mock_db()

    listing_result = MagicMock()
    listing_result.fetchone.return_value = _listing_row(price=price)

    art_result = MagicMock()
    art_result.fetchone.return_value = _art_row()

    deduct_update = MagicMock()
    deduct_update.fetchone.return_value = _row(500)
    deduct_insert = MagicMock()

    credit_update = MagicMock()
    credit_update.fetchone.return_value = _row(900)
    credit_insert = MagicMock()

    transfer_update = MagicMock()
    ownership_insert = MagicMock()
    listing_sold = MagicMock()
    txn_insert = MagicMock()

    db.execute.side_effect = [
        listing_result,
        art_result,
        deduct_update,
        deduct_insert,
        credit_update,
        credit_insert,
        transfer_update,
        ownership_insert,
        listing_sold,
        txn_insert,
    ]

    await execute_trade(db, BUYER_ID, LISTING_ID)

    # add_credits call is the 5th execute (index 4). Check the amount param.
    # The seller payout is passed to add_credits as 'amount' arg.
    # add_credits issues UPDATE ... SET credit_balance = credit_balance + :amount
    # That's the 5th db.execute call (index 4), params dict has 'amount'.
    add_credits_call = db.execute.call_args_list[4]
    seller_payout_param = add_credits_call[0][1]["amount"]
    assert seller_payout_param == expected_payout

    # The INSERT transactions call is last (index 9).
    txn_call = db.execute.call_args_list[9]
    txn_params = txn_call[0][1]
    assert txn_params["platform_fee_cents"] == expected_fee
    assert txn_params["seller_payout_cents"] == expected_payout
    assert txn_params["amount_cents"] == price
