"""Credit management service -- balance queries, atomic deductions, and refunds."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class CreditPackResponse(BaseModel):
    pack_id: int
    name: str
    price_cents: int
    credit_amount: int


class CreditBalanceResponse(BaseModel):
    user_id: uuid.UUID
    credit_balance: int


class PurchaseRequest(BaseModel):
    pack_id: int


# ---------------------------------------------------------------------------
# Service functions
# ---------------------------------------------------------------------------

async def get_available_packs(db: AsyncSession) -> list[CreditPackResponse]:
    """Return all active credit pack definitions."""
    result = await db.execute(
        text(
            "SELECT pack_id, name, price_cents, credit_amount "
            "FROM credit_pack_definitions "
            "WHERE is_active = true "
            "ORDER BY price_cents"
        )
    )
    rows = result.fetchall()
    return [
        CreditPackResponse(
            pack_id=row[0],
            name=row[1],
            price_cents=row[2],
            credit_amount=row[3],
        )
        for row in rows
    ]


async def atomic_deduct_credits(
    db: AsyncSession,
    user_id: uuid.UUID,
    amount: int,
    txn_type: str,
    reference_id: uuid.UUID | None = None,
) -> int:
    """Atomically deduct credits using UPDATE ... WHERE balance >= amount.

    This avoids SELECT FOR UPDATE and instead relies on the atomic
    conditional update to prevent race conditions and overdrafts.

    Returns the new balance on success.
    Raises HTTPException(402) if the user has insufficient credits.
    """
    result = await db.execute(
        text(
            "UPDATE users "
            "SET credit_balance = credit_balance - :amount "
            "WHERE user_id = :user_id AND credit_balance >= :amount "
            "RETURNING credit_balance"
        ),
        {"user_id": user_id, "amount": amount},
    )
    row = result.fetchone()
    if row is None:
        raise HTTPException(status_code=402, detail="Insufficient credits")

    new_balance: int = row[0]

    # Record the transaction (negative amount for deduction)
    await db.execute(
        text(
            "INSERT INTO credit_transactions (user_id, amount, txn_type, reference_id, created_at) "
            "VALUES (:user_id, :amount, :txn_type, :reference_id, :created_at)"
        ),
        {
            "user_id": user_id,
            "amount": -amount,
            "txn_type": txn_type,
            "reference_id": reference_id,
            "created_at": datetime.now(timezone.utc),
        },
    )

    return new_balance


async def add_credits(
    db: AsyncSession,
    user_id: uuid.UUID,
    amount: int,
    txn_type: str,
    reference_id: uuid.UUID | None = None,
) -> int:
    """Add credits to a user's balance and record the transaction.

    Returns the new balance.
    """
    result = await db.execute(
        text(
            "UPDATE users "
            "SET credit_balance = credit_balance + :amount "
            "WHERE user_id = :user_id "
            "RETURNING credit_balance"
        ),
        {"user_id": user_id, "amount": amount},
    )
    row = result.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="User not found")

    new_balance: int = row[0]

    # Record the transaction (positive amount for addition)
    await db.execute(
        text(
            "INSERT INTO credit_transactions (user_id, amount, txn_type, reference_id, created_at) "
            "VALUES (:user_id, :amount, :txn_type, :reference_id, :created_at)"
        ),
        {
            "user_id": user_id,
            "amount": amount,
            "txn_type": txn_type,
            "reference_id": reference_id,
            "created_at": datetime.now(timezone.utc),
        },
    )

    return new_balance


async def refund_credits(
    db: AsyncSession,
    user_id: uuid.UUID,
    amount: int,
    reference_id: uuid.UUID | None = None,
) -> int:
    """Refund credits to a user. Delegates to add_credits with txn_type='refund'."""
    return await add_credits(
        db=db,
        user_id=user_id,
        amount=amount,
        txn_type="refund",
        reference_id=reference_id,
    )


async def get_balance(db: AsyncSession, user_id: uuid.UUID) -> int:
    """Return the current credit balance for a user."""
    result = await db.execute(
        text("SELECT credit_balance FROM users WHERE user_id = :user_id"),
        {"user_id": user_id},
    )
    row = result.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="User not found")
    return row[0]
