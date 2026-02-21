"""Free tier service -- eligibility checks and usage tracking.

Free tier allows phone-verified users to generate 1 free pixel art piece
per day. Free art is non-tradeable (cannot be listed on the marketplace).
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def check_free_tier_eligibility(db: AsyncSession, user_id: uuid.UUID) -> dict:
    """Check if user can use free tier. Returns eligibility info.

    Raises HTTPException(403) if the user is not phone-verified.
    Raises HTTPException(429) if the user has already used their free
    generation today.
    """
    result = await db.execute(
        text(
            "SELECT phone_verified, free_generations_today, last_free_gen_date "
            "FROM users WHERE user_id = :user_id"
        ),
        {"user_id": user_id},
    )
    row = result.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="User not found")

    phone_verified = row[0]
    free_generations_today = row[1]
    last_free_gen_date = row[2]

    if not phone_verified:
        raise HTTPException(
            status_code=403,
            detail="Phone verification required for free tier",
        )

    today = date.today()

    # Reset counter if last free generation was on a different day
    if last_free_gen_date is None or last_free_gen_date != today:
        free_generations_today = 0

    if free_generations_today >= 1:
        raise HTTPException(
            status_code=429,
            detail="Free generation limit reached for today",
        )

    return {
        "eligible": True,
        "free_generations_today": free_generations_today,
    }


async def record_free_generation(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Record that a free generation was used today."""
    today = date.today()
    await db.execute(
        text(
            "UPDATE users "
            "SET free_generations_today = free_generations_today + 1, "
            "last_free_gen_date = :today "
            "WHERE user_id = :user_id"
        ),
        {"user_id": user_id, "today": today},
    )
