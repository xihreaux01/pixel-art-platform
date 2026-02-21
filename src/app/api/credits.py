"""Credit pack and balance API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.database import get_db
from app.models import User
from app.services.credit_service import (
    CreditBalanceResponse,
    CreditPackResponse,
    PurchaseRequest,
    get_available_packs,
    get_balance,
)
from app.services.payment_service import create_checkout_session

router = APIRouter(prefix="/api/v1/credits", tags=["credits"])


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/packs", response_model=list[CreditPackResponse])
async def list_packs(db: AsyncSession = Depends(get_db)):
    """Return all active credit packs available for purchase."""
    return await get_available_packs(db)


@router.get("/balance", response_model=CreditBalanceResponse)
async def read_balance(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the authenticated user's current credit balance."""
    balance = await get_balance(db, current_user.user_id)
    return CreditBalanceResponse(
        user_id=current_user.user_id, credit_balance=balance,
    )


@router.post("/purchase")
async def purchase_credits(
    body: PurchaseRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a Stripe Checkout Session for the requested credit pack."""
    url = await create_checkout_session(db, current_user.user_id, body.pack_id)
    return {"checkout_url": url}
