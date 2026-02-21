"""Marketplace listing CRUD with ownership validation and trade execution."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.credit_service import add_credits, atomic_deduct_credits


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class CreateListingRequest(BaseModel):
    art_id: uuid.UUID
    asking_price_cents: int


class ListingResponse(BaseModel):
    listing_id: uuid.UUID
    art_id: uuid.UUID
    seller_user_id: uuid.UUID
    asking_price_cents: int
    currency_code: str
    status: str
    listed_at: datetime
    sold_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None


class BrowseListingsResponse(BaseModel):
    listings: list[ListingResponse]
    next_cursor: Optional[str] = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _row_to_listing(row) -> ListingResponse:
    """Map a database row to a ListingResponse."""
    return ListingResponse(
        listing_id=row[0],
        art_id=row[1],
        seller_user_id=row[2],
        asking_price_cents=row[3],
        currency_code=row[4],
        status=row[5],
        listed_at=row[6],
        sold_at=row[7],
        cancelled_at=row[8],
    )


_LISTING_COLUMNS = (
    "listing_id, art_id, seller_user_id, asking_price_cents, "
    "currency_code, status, listed_at, sold_at, cancelled_at"
)


async def _lock_and_validate_art(
    db: AsyncSession,
    art_id: uuid.UUID,
    seller_user_id: uuid.UUID,
) -> None:
    """Lock the art piece row and validate ownership/tradeability/listed state."""
    result = await db.execute(
        text(
            "SELECT current_owner_id, is_tradeable, is_marketplace_listed "
            "FROM art_pieces WHERE art_id = :art_id FOR UPDATE"
        ),
        {"art_id": art_id},
    )
    row = result.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Art piece not found")

    current_owner_id, is_tradeable, is_marketplace_listed = row[0], row[1], row[2]

    if current_owner_id != seller_user_id:
        raise HTTPException(status_code=403, detail="You do not own this art piece")
    if not is_tradeable:
        raise HTTPException(status_code=400, detail="This art piece is not tradeable")
    if is_marketplace_listed:
        raise HTTPException(
            status_code=409, detail="This art piece is already listed on the marketplace",
        )


async def _insert_listing(
    db: AsyncSession,
    art_id: uuid.UUID,
    seller_user_id: uuid.UUID,
    asking_price_cents: int,
) -> uuid.UUID:
    """Mark art as listed and insert the marketplace_listings row."""
    await db.execute(
        text("UPDATE art_pieces SET is_marketplace_listed = true WHERE art_id = :art_id"),
        {"art_id": art_id},
    )
    listing_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO marketplace_listings "
            "(listing_id, art_id, seller_user_id, asking_price_cents, "
            "currency_code, status, listed_at) "
            "VALUES (:listing_id, :art_id, :seller_user_id, "
            ":asking_price_cents, 'USD', 'active', :listed_at)"
        ),
        {
            "listing_id": listing_id,
            "art_id": art_id,
            "seller_user_id": seller_user_id,
            "asking_price_cents": asking_price_cents,
            "listed_at": datetime.now(timezone.utc),
        },
    )
    return listing_id


async def _fetch_listing_for_cancel(
    db: AsyncSession,
    listing_id: uuid.UUID,
    user_id: uuid.UUID,
) -> tuple[str, uuid.UUID]:
    """Fetch and validate a listing for cancellation. Returns (status, art_id)."""
    result = await db.execute(
        text(
            "SELECT seller_user_id, status, art_id "
            "FROM marketplace_listings WHERE listing_id = :listing_id"
        ),
        {"listing_id": listing_id},
    )
    row = result.fetchone()

    if row is None or row[0] != user_id:
        raise HTTPException(status_code=404, detail="Listing not found")
    if row[1] != "active":
        raise HTTPException(status_code=409, detail="Listing is not active")

    return row[1], row[2]


async def _execute_cancel(
    db: AsyncSession,
    listing_id: uuid.UUID,
    art_id: uuid.UUID,
) -> None:
    """Set listing to cancelled and unmark the art piece."""
    now = datetime.now(timezone.utc)
    await db.execute(
        text(
            "UPDATE marketplace_listings "
            "SET status = 'cancelled', cancelled_at = :cancelled_at "
            "WHERE listing_id = :listing_id"
        ),
        {"listing_id": listing_id, "cancelled_at": now},
    )
    await db.execute(
        text("UPDATE art_pieces SET is_marketplace_listed = false WHERE art_id = :art_id"),
        {"art_id": art_id},
    )


async def _query_active_listings(
    db: AsyncSession,
    cursor: Optional[str],
    fetch_limit: int,
):
    """Run the paginated query for active listings."""
    if cursor is not None:
        cursor_uuid = uuid.UUID(cursor)
        return await db.execute(
            text(
                f"SELECT {_LISTING_COLUMNS} FROM marketplace_listings "
                "WHERE status = 'active' AND (listed_at, listing_id) < ("
                "  SELECT listed_at, listing_id FROM marketplace_listings"
                "  WHERE listing_id = :cursor_id"
                ") ORDER BY listed_at DESC, listing_id DESC LIMIT :fetch_limit"
            ),
            {"cursor_id": cursor_uuid, "fetch_limit": fetch_limit},
        )
    return await db.execute(
        text(
            f"SELECT {_LISTING_COLUMNS} FROM marketplace_listings "
            "WHERE status = 'active' "
            "ORDER BY listed_at DESC, listing_id DESC LIMIT :fetch_limit"
        ),
        {"fetch_limit": fetch_limit},
    )


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------

async def create_listing(
    db: AsyncSession,
    seller_user_id: uuid.UUID,
    art_id: uuid.UUID,
    asking_price_cents: int,
) -> uuid.UUID:
    """Create a marketplace listing for an art piece.

    Returns the new listing_id.
    """
    await _lock_and_validate_art(db, art_id, seller_user_id)
    return await _insert_listing(db, art_id, seller_user_id, asking_price_cents)


async def cancel_listing(
    db: AsyncSession,
    listing_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    """Cancel an active marketplace listing."""
    _status, art_id = await _fetch_listing_for_cancel(db, listing_id, user_id)
    await _execute_cancel(db, listing_id, art_id)


async def get_listing(
    db: AsyncSession,
    listing_id: uuid.UUID,
) -> ListingResponse:
    """Get a single listing by ID."""
    result = await db.execute(
        text(f"SELECT {_LISTING_COLUMNS} FROM marketplace_listings "
             "WHERE listing_id = :listing_id"),
        {"listing_id": listing_id},
    )
    row = result.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    return _row_to_listing(row)


async def browse_listings(
    db: AsyncSession,
    cursor: Optional[str] = None,
    limit: int = 50,
) -> BrowseListingsResponse:
    """Browse active listings with cursor-based pagination (max 100)."""
    limit = min(max(limit, 1), 100)
    result = await _query_active_listings(db, cursor, limit + 1)
    rows = result.fetchall()

    has_next = len(rows) > limit
    rows = rows[:limit] if has_next else rows

    listings = [_row_to_listing(r) for r in rows]
    next_cursor = str(listings[-1].listing_id) if has_next and listings else None
    return BrowseListingsResponse(listings=listings, next_cursor=next_cursor)


# ---------------------------------------------------------------------------
# Trade execution helpers
# ---------------------------------------------------------------------------

async def _lock_listing_for_trade(
    db: AsyncSession,
    listing_id: uuid.UUID,
    buyer_user_id: uuid.UUID,
) -> tuple[uuid.UUID, uuid.UUID, int]:
    """Lock listing + art piece and validate for trade.

    Returns (art_id, seller_user_id, asking_price_cents).
    """
    result = await db.execute(
        text(
            "SELECT listing_id, art_id, seller_user_id, asking_price_cents, status "
            "FROM marketplace_listings WHERE listing_id = :listing_id FOR UPDATE"
        ),
        {"listing_id": listing_id},
    )
    row = result.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Listing not found")

    art_id, seller_user_id, asking_price_cents, listing_status = (
        row[1], row[2], row[3], row[4],
    )

    if listing_status != "active":
        raise HTTPException(status_code=409, detail="Listing is not active")
    if buyer_user_id == seller_user_id:
        raise HTTPException(status_code=400, detail="Cannot buy your own art")

    # Lock the art piece row
    await db.execute(
        text(
            "SELECT art_id, current_owner_id "
            "FROM art_pieces WHERE art_id = :art_id FOR UPDATE"
        ),
        {"art_id": art_id},
    )

    return art_id, seller_user_id, asking_price_cents


async def _transfer_ownership(
    db: AsyncSession,
    art_id: uuid.UUID,
    buyer_id: uuid.UUID,
    seller_id: uuid.UUID,
    transaction_id: uuid.UUID,
) -> None:
    """Update art piece owner and record ownership history."""
    await db.execute(
        text(
            "UPDATE art_pieces "
            "SET current_owner_id = :buyer_id, "
            "is_marketplace_listed = false, "
            "times_traded = times_traded + 1 "
            "WHERE art_id = :art_id"
        ),
        {"buyer_id": buyer_id, "art_id": art_id},
    )
    # TODO: Re-encode watermark with new owner (needs file I/O integration)
    await db.execute(
        text(
            "INSERT INTO ownership_history "
            "(art_id, from_user_id, to_user_id, transfer_type, "
            "transaction_id, transferred_at) "
            "VALUES (:art_id, :from_user_id, :to_user_id, 'trade', "
            ":transaction_id, :transferred_at)"
        ),
        {
            "art_id": art_id,
            "from_user_id": seller_id,
            "to_user_id": buyer_id,
            "transaction_id": transaction_id,
            "transferred_at": datetime.now(timezone.utc),
        },
    )


class TradeRecord(BaseModel):
    """Value object carrying all identifiers and amounts for a completed trade."""
    transaction_id: uuid.UUID
    listing_id: uuid.UUID
    buyer_user_id: uuid.UUID
    seller_user_id: uuid.UUID
    art_id: uuid.UUID
    amount_cents: int
    platform_fee_cents: int
    seller_payout_cents: int


async def _mark_listing_sold(
    db: AsyncSession,
    listing_id: uuid.UUID,
) -> None:
    """Set listing status to sold with current timestamp."""
    await db.execute(
        text(
            "UPDATE marketplace_listings "
            "SET status = 'sold', sold_at = :now "
            "WHERE listing_id = :listing_id"
        ),
        {"listing_id": listing_id, "now": datetime.now(timezone.utc)},
    )


async def _insert_transaction(
    db: AsyncSession,
    record: TradeRecord,
) -> None:
    """Insert a completed transaction row from trade record."""
    now = datetime.now(timezone.utc)
    await db.execute(
        text(
            "INSERT INTO transactions "
            "(transaction_id, buyer_user_id, seller_user_id, art_id, "
            "listing_id, amount_cents, platform_fee_cents, "
            "seller_payout_cents, status, initiated_at, completed_at) "
            "VALUES (:transaction_id, :buyer_user_id, :seller_user_id, "
            ":art_id, :listing_id, :amount_cents, :platform_fee_cents, "
            ":seller_payout_cents, 'completed', :initiated_at, :completed_at)"
        ),
        {
            **record.model_dump(),
            "initiated_at": now,
            "completed_at": now,
        },
    )


# ---------------------------------------------------------------------------
# Public trade function
# ---------------------------------------------------------------------------

async def execute_trade(
    db: AsyncSession,
    buyer_user_id: uuid.UUID,
    listing_id: uuid.UUID,
) -> uuid.UUID:
    """Execute a full trade workflow: lock, validate, transfer, and record.

    Returns the transaction_id on success.
    """
    art_id, seller_user_id, asking_price_cents = await _lock_listing_for_trade(
        db, listing_id, buyer_user_id,
    )

    # Calculate fees: 10% platform fee
    platform_fee_cents = asking_price_cents // 10
    seller_payout_cents = asking_price_cents - platform_fee_cents

    # Deduct buyer credits (raises 402 on insufficient balance)
    await atomic_deduct_credits(
        db, buyer_user_id, asking_price_cents, "marketplace_purchase", listing_id,
    )

    # Credit seller earnings
    await add_credits(
        db, seller_user_id, seller_payout_cents, "marketplace_earning", listing_id,
    )

    # Transfer ownership
    transaction_id = uuid.uuid4()
    await _transfer_ownership(db, art_id, buyer_user_id, seller_user_id, transaction_id)

    # Build trade record and finalise
    record = TradeRecord(
        transaction_id=transaction_id,
        listing_id=listing_id,
        buyer_user_id=buyer_user_id,
        seller_user_id=seller_user_id,
        art_id=art_id,
        amount_cents=asking_price_cents,
        platform_fee_cents=platform_fee_cents,
        seller_payout_cents=seller_payout_cents,
    )
    await _mark_listing_sold(db, listing_id)
    await _insert_transaction(db, record)

    return transaction_id
