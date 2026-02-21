"""Tests for marketplace service functions."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.services.marketplace_service import (
    browse_listings,
    cancel_listing,
    create_listing,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SELLER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
OTHER_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
ART_ID = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
LISTING_ID = uuid.UUID("00000000-0000-0000-0000-0000000000b1")


def _mock_db():
    """Return an AsyncMock that behaves like an AsyncSession."""
    return AsyncMock()


def _row(*values):
    """Create a lightweight tuple-like object returned by fetchone."""
    return values


# ---------------------------------------------------------------------------
# test_create_listing_success
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_listing_success():
    """Owner creates a listing for tradeable, unlisted art."""
    db = _mock_db()

    # SELECT FOR UPDATE returns: current_owner_id, is_tradeable, is_marketplace_listed
    select_result = MagicMock()
    select_result.fetchone.return_value = _row(SELLER_ID, True, False)

    # UPDATE art_pieces SET is_marketplace_listed = true
    update_result = MagicMock()

    # INSERT into marketplace_listings
    insert_result = MagicMock()

    db.execute.side_effect = [select_result, update_result, insert_result]

    listing_id = await create_listing(
        db=db,
        seller_user_id=SELLER_ID,
        art_id=ART_ID,
        asking_price_cents=1500,
    )

    assert isinstance(listing_id, uuid.UUID)
    assert db.execute.call_count == 3


# ---------------------------------------------------------------------------
# test_create_listing_not_owner
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_listing_not_owner():
    """Non-owner gets 403 when trying to create a listing."""
    db = _mock_db()

    select_result = MagicMock()
    select_result.fetchone.return_value = _row(SELLER_ID, True, False)
    db.execute.return_value = select_result

    with pytest.raises(HTTPException) as exc_info:
        await create_listing(
            db=db,
            seller_user_id=OTHER_USER_ID,
            art_id=ART_ID,
            asking_price_cents=1500,
        )

    assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# test_create_listing_non_tradeable
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_listing_non_tradeable():
    """Free (non-tradeable) art gets 400."""
    db = _mock_db()

    select_result = MagicMock()
    select_result.fetchone.return_value = _row(SELLER_ID, False, False)
    db.execute.return_value = select_result

    with pytest.raises(HTTPException) as exc_info:
        await create_listing(
            db=db,
            seller_user_id=SELLER_ID,
            art_id=ART_ID,
            asking_price_cents=1500,
        )

    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# test_create_listing_already_listed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_listing_already_listed():
    """Already-listed art gets 409."""
    db = _mock_db()

    select_result = MagicMock()
    select_result.fetchone.return_value = _row(SELLER_ID, True, True)
    db.execute.return_value = select_result

    with pytest.raises(HTTPException) as exc_info:
        await create_listing(
            db=db,
            seller_user_id=SELLER_ID,
            art_id=ART_ID,
            asking_price_cents=1500,
        )

    assert exc_info.value.status_code == 409


# ---------------------------------------------------------------------------
# test_cancel_listing_success
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_listing_success():
    """Owner cancels an active listing successfully."""
    db = _mock_db()

    # SELECT returns: seller_user_id, status, art_id
    select_result = MagicMock()
    select_result.fetchone.return_value = _row(SELLER_ID, "active", ART_ID)

    # UPDATE marketplace_listings SET status='cancelled'
    update_listing_result = MagicMock()

    # UPDATE art_pieces SET is_marketplace_listed = false
    update_art_result = MagicMock()

    db.execute.side_effect = [
        select_result,
        update_listing_result,
        update_art_result,
    ]

    await cancel_listing(db=db, listing_id=LISTING_ID, user_id=SELLER_ID)

    assert db.execute.call_count == 3


# ---------------------------------------------------------------------------
# test_cancel_listing_not_active
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_listing_not_active():
    """Cancel on a non-active listing gets 409."""
    db = _mock_db()

    select_result = MagicMock()
    select_result.fetchone.return_value = _row(SELLER_ID, "sold", ART_ID)
    db.execute.return_value = select_result

    with pytest.raises(HTTPException) as exc_info:
        await cancel_listing(db=db, listing_id=LISTING_ID, user_id=SELLER_ID)

    assert exc_info.value.status_code == 409


# ---------------------------------------------------------------------------
# test_browse_listings_pagination
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_browse_listings_pagination():
    """Pagination returns correct listings and next_cursor."""
    db = _mock_db()
    now = datetime.now(timezone.utc)

    # Build 3 rows; limit=2 so row 3 triggers next_cursor
    ids = [uuid.uuid4() for _ in range(3)]
    rows = [
        _row(ids[i], ART_ID, SELLER_ID, 1000 + i, "USD", "active", now, None, None)
        for i in range(3)
    ]

    query_result = MagicMock()
    query_result.fetchall.return_value = rows
    db.execute.return_value = query_result

    resp = await browse_listings(db=db, cursor=None, limit=2)

    assert len(resp.listings) == 2
    assert resp.next_cursor == str(ids[1])
