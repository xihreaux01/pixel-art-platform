"""Marketplace listing API endpoints."""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.database import get_db
from app.models import User
from app.services.marketplace_service import (
    BrowseListingsResponse,
    CreateListingRequest,
    ListingResponse,
    browse_listings,
    cancel_listing,
    create_listing,
    execute_trade,
    get_listing,
)

router = APIRouter(prefix="/api/v1/marketplace", tags=["marketplace"])


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/", status_code=201)
async def create_listing_endpoint(
    body: CreateListingRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new marketplace listing for an art piece."""
    listing_id = await create_listing(
        db=db,
        seller_user_id=current_user.user_id,
        art_id=body.art_id,
        asking_price_cents=body.asking_price_cents,
    )
    return {"listing_id": str(listing_id)}


@router.delete("/{listing_id}", status_code=200)
async def cancel_listing_endpoint(
    listing_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Cancel an active marketplace listing."""
    await cancel_listing(db=db, listing_id=listing_id, user_id=current_user.user_id)
    return {"detail": "Listing cancelled"}


@router.get("/{listing_id}", response_model=ListingResponse)
async def get_listing_endpoint(
    listing_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get details for a single marketplace listing."""
    return await get_listing(db=db, listing_id=listing_id)


@router.get("/", response_model=BrowseListingsResponse)
async def browse_listings_endpoint(
    cursor: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Browse active marketplace listings with cursor-based pagination."""
    return await browse_listings(db=db, cursor=cursor, limit=limit)


@router.post("/{listing_id}/buy", status_code=status.HTTP_200_OK)
async def buy_listing(
    listing_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Purchase a marketplace listing."""
    transaction_id = await execute_trade(db, current_user.user_id, listing_id)
    await db.commit()
    return {"transaction_id": str(transaction_id), "status": "completed"}
