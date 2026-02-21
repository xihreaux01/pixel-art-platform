"""Stripe payment integration -- checkout sessions and webhook handling."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import stripe
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.credit_service import add_credits

stripe.api_key = settings.STRIPE_SECRET_KEY

# ---------------------------------------------------------------------------
# Success / Cancel URLs  (placeholder -- override via env or settings later)
# ---------------------------------------------------------------------------
SUCCESS_URL = "https://pixelart.example.com/purchase/success?session_id={CHECKOUT_SESSION_ID}"
CANCEL_URL = "https://pixelart.example.com/purchase/cancel"


# ---------------------------------------------------------------------------
# Checkout session creation
# ---------------------------------------------------------------------------

async def create_checkout_session(
    db: AsyncSession,
    user_id: uuid.UUID,
    pack_id: int,
) -> str:
    """Create a Stripe Checkout Session for a credit pack purchase.

    Returns the Stripe Checkout Session URL.
    """
    result = await db.execute(
        text(
            "SELECT pack_id, name, price_cents, credit_amount "
            "FROM credit_pack_definitions "
            "WHERE pack_id = :pack_id AND is_active = true"
        ),
        {"pack_id": pack_id},
    )
    pack = result.fetchone()
    if pack is None:
        raise HTTPException(status_code=404, detail="Credit pack not found")

    pack_name: str = pack[1]
    price_cents: int = pack[2]

    session = stripe.checkout.Session.create(
        line_items=[
            {
                "price_data": {
                    "currency": "usd",
                    "unit_amount": price_cents,
                    "product_data": {"name": pack_name},
                },
                "quantity": 1,
            }
        ],
        mode="payment",
        metadata={
            "user_id": str(user_id),
            "pack_id": str(pack_id),
        },
        success_url=SUCCESS_URL,
        cancel_url=CANCEL_URL,
    )

    return session.url


# ---------------------------------------------------------------------------
# Webhook helpers
# ---------------------------------------------------------------------------

def _verify_stripe_event(payload: bytes, sig_header: str) -> dict:
    """Verify the Stripe webhook signature and return the parsed event."""
    try:
        return stripe.Webhook.construct_event(
            payload,
            sig_header,
            settings.STRIPE_WEBHOOK_SECRET,
        )
    except stripe.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")


async def _is_already_processed(db: AsyncSession, event_id: str) -> bool:
    """Return True if this webhook event has already been handled."""
    existing = await db.execute(
        text("SELECT 1 FROM processed_webhooks WHERE event_id = :event_id"),
        {"event_id": event_id},
    )
    return existing.fetchone() is not None


async def _process_checkout_completed(
    db: AsyncSession,
    session_obj: dict,
    event_id: str,
) -> None:
    """Fulfil a completed checkout: credit user, record payment, mark processed."""
    metadata = session_obj.get("metadata", {})
    user_id = uuid.UUID(metadata["user_id"])
    pack_id = int(metadata["pack_id"])

    pack_result = await db.execute(
        text(
            "SELECT credit_amount, price_cents "
            "FROM credit_pack_definitions "
            "WHERE pack_id = :pack_id"
        ),
        {"pack_id": pack_id},
    )
    pack = pack_result.fetchone()
    if pack is None:
        raise HTTPException(status_code=400, detail="Pack not found for webhook")

    reference_id = uuid.uuid4()
    await add_credits(
        db=db, user_id=user_id, amount=pack[0],
        txn_type="purchase", reference_id=reference_id,
    )

    await _insert_payment_record(
        db, reference_id, user_id, pack[1], session_obj.get("payment_intent"),
    )
    await _mark_event_processed(db, event_id)


async def _insert_payment_record(
    db: AsyncSession,
    payment_id: uuid.UUID,
    user_id: uuid.UUID,
    amount_cents: int,
    stripe_pi_id: str | None,
) -> None:
    """Insert a payment_records row for a completed credit purchase."""
    await db.execute(
        text(
            "INSERT INTO payment_records "
            "(payment_id, user_id, payment_type, amount_cents, "
            "stripe_payment_intent_id, status, created_at) "
            "VALUES (:payment_id, :user_id, :payment_type, :amount_cents, "
            ":stripe_payment_intent_id, :status, :created_at)"
        ),
        {
            "payment_id": payment_id,
            "user_id": user_id,
            "payment_type": "credit_purchase",
            "amount_cents": amount_cents,
            "stripe_payment_intent_id": stripe_pi_id,
            "status": "completed",
            "created_at": datetime.now(timezone.utc),
        },
    )


async def _mark_event_processed(db: AsyncSession, event_id: str) -> None:
    """Record a webhook event ID so it is not replayed."""
    await db.execute(
        text(
            "INSERT INTO processed_webhooks (event_id, processed_at) "
            "VALUES (:event_id, :processed_at)"
        ),
        {"event_id": event_id, "processed_at": datetime.now(timezone.utc)},
    )


# ---------------------------------------------------------------------------
# Webhook entry-point
# ---------------------------------------------------------------------------

async def handle_webhook(
    payload: bytes,
    sig_header: str,
    db: AsyncSession,
) -> None:
    """Verify a Stripe webhook signature and process the event.

    Idempotent -- skips events that have already been processed.
    """
    event = _verify_stripe_event(payload, sig_header)
    event_id: str = event["id"]

    if await _is_already_processed(db, event_id):
        return

    if event["type"] == "checkout.session.completed":
        await _process_checkout_completed(db, event["data"]["object"], event_id)
