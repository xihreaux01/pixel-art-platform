"""Stripe webhook endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.payment_service import handle_webhook

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


@router.post("/stripe")
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Receive and process Stripe webhook events.

    Reads the raw request body and the Stripe-Signature header,
    then delegates to the payment service for verification and handling.
    """
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    await handle_webhook(payload, sig_header, db)
    return {"status": "ok"}
