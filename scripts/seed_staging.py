#!/usr/bin/env python3
"""Seed the staging database with sample data.

Usage:
    DATABASE_URL=postgresql://app:devpassword@localhost:5432/pixelart_staging \
        python scripts/seed_staging.py

The script is idempotent -- it checks for existing data before inserting.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid

import asyncpg
import bcrypt

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Accept the async SQLAlchemy-style URL but strip the driver prefix for asyncpg.
_RAW_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://app:devpassword@db:5432/pixelart_staging",
)
DATABASE_URL = _RAW_URL.replace("postgresql+asyncpg://", "postgresql://")

# Deterministic UUIDs so the script stays idempotent across runs.
USER_IDS = {
    "admin": uuid.UUID("00000000-0000-4000-a000-000000000001"),
    "seller1": uuid.UUID("00000000-0000-4000-a000-000000000002"),
    "seller2": uuid.UUID("00000000-0000-4000-a000-000000000003"),
    "buyer1": uuid.UUID("00000000-0000-4000-a000-000000000004"),
    "buyer2": uuid.UUID("00000000-0000-4000-a000-000000000005"),
}

ART_IDS = {
    "art1": uuid.UUID("00000000-0000-4000-b000-000000000001"),
    "art2": uuid.UUID("00000000-0000-4000-b000-000000000002"),
    "art3": uuid.UUID("00000000-0000-4000-b000-000000000003"),
}

LISTING_IDS = {
    "listing1": uuid.UUID("00000000-0000-4000-c000-000000000001"),
    "listing2": uuid.UUID("00000000-0000-4000-c000-000000000002"),
}

_BCRYPT_ROUNDS = 12


def _hash(password: str) -> str:
    salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


# ---------------------------------------------------------------------------
# Seed functions
# ---------------------------------------------------------------------------


async def seed_users(conn: asyncpg.Connection) -> None:
    """Insert sample users if they do not already exist."""
    users = [
        (USER_IDS["admin"], "admin@example.com", "admin", "admin123", 10000),
        (USER_IDS["seller1"], "seller1@example.com", "seller1", "password1", 5000),
        (USER_IDS["seller2"], "seller2@example.com", "seller2", "password2", 5000),
        (USER_IDS["buyer1"], "buyer1@example.com", "buyer1", "password1", 2000),
        (USER_IDS["buyer2"], "buyer2@example.com", "buyer2", "password2", 2000),
    ]

    for uid, email, username, password, balance in users:
        exists = await conn.fetchval(
            "SELECT 1 FROM users WHERE user_id = $1", uid
        )
        if exists:
            print(f"  [skip] User {email} already exists")
            continue

        pw_hash = _hash(password)
        await conn.execute(
            """
            INSERT INTO users (user_id, email, username, password_hash, credit_balance)
            VALUES ($1, $2, $3, $4, $5)
            """,
            uid,
            email,
            username,
            pw_hash,
            balance,
        )
        print(f"  [created] User {email}  (balance={balance})")


async def seed_generation_tiers(conn: asyncpg.Connection) -> None:
    """Insert generation tier definitions if the table is empty."""
    count = await conn.fetchval("SELECT count(*) FROM generation_tier_definitions")
    if count > 0:
        print(f"  [skip] generation_tier_definitions already has {count} rows")
        return

    tiers = [
        ("free", 32, 32, 0, 30, 50, 60,
         json.dumps(["draw_pixel", "draw_line", "fill_rect", "fill_bucket", "set_palette"])),
        ("basic", 64, 64, 50, 150, 200, 300,
         json.dumps(["draw_pixel", "draw_line", "fill_rect", "fill_bucket",
                      "set_palette", "draw_circle", "draw_ellipse"])),
        ("premium", 128, 128, 200, 400, 500, 600,
         json.dumps(["draw_pixel", "draw_line", "fill_rect", "fill_bucket",
                      "set_palette", "draw_circle", "draw_ellipse",
                      "draw_bezier", "gradient_fill"])),
    ]

    for name, w, h, cost, soft, hard, timeout, tools in tiers:
        await conn.execute(
            """
            INSERT INTO generation_tier_definitions
                (tier_name, canvas_width, canvas_height, credit_cost,
                 tool_budget_soft, tool_budget_hard, job_timeout_seconds, allowed_tools)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
            """,
            name, w, h, cost, soft, hard, timeout, tools,
        )
        print(f"  [created] Tier {name}  (cost={cost} credits)")


async def seed_credit_packs(conn: asyncpg.Connection) -> None:
    """Insert credit pack definitions if the table is empty."""
    count = await conn.fetchval("SELECT count(*) FROM credit_pack_definitions")
    if count > 0:
        print(f"  [skip] credit_pack_definitions already has {count} rows")
        return

    packs = [
        ("Starter", 499, 500),
        ("Pro", 1499, 2000),
        ("Mega", 2999, 5000),
    ]

    for name, price_cents, credits in packs:
        await conn.execute(
            """
            INSERT INTO credit_pack_definitions (name, price_cents, credit_amount, is_active)
            VALUES ($1, $2, $3, TRUE)
            """,
            name, price_cents, credits,
        )
        print(f"  [created] Pack {name}  ({credits} credits / ${price_cents / 100:.2f})")


async def seed_art_pieces(conn: asyncpg.Connection) -> None:
    """Insert sample art pieces if they do not already exist."""
    pieces = [
        (
            ART_IDS["art1"],
            USER_IDS["seller1"],
            USER_IDS["seller1"],
            "basic",
            64, 64,
            "deepseek-r1",
            "1.0",
            "/var/art/staging/art1.png",
            "/var/art/staging/art1_thumb.png",
            hashlib.sha256(b"staging-art-piece-1").hexdigest(),
            "staging-seal-signature-1",
            True,
            False,
        ),
        (
            ART_IDS["art2"],
            USER_IDS["seller2"],
            USER_IDS["seller2"],
            "premium",
            128, 128,
            "deepseek-r1",
            "1.0",
            "/var/art/staging/art2.png",
            "/var/art/staging/art2_thumb.png",
            hashlib.sha256(b"staging-art-piece-2").hexdigest(),
            "staging-seal-signature-2",
            True,
            False,
        ),
        (
            ART_IDS["art3"],
            USER_IDS["seller1"],
            USER_IDS["seller1"],
            "basic",
            64, 64,
            "deepseek-r1",
            "1.0",
            "/var/art/staging/art3.png",
            "/var/art/staging/art3_thumb.png",
            hashlib.sha256(b"staging-art-piece-3").hexdigest(),
            "staging-seal-signature-3",
            True,
            False,
        ),
    ]

    for (art_id, creator_id, owner_id, tier, w, h, model, ver,
         img_path, thumb_path, gen_hash, seal, tradeable, listed) in pieces:
        exists = await conn.fetchval(
            "SELECT 1 FROM art_pieces WHERE art_id = $1", art_id
        )
        if exists:
            print(f"  [skip] Art piece {art_id} already exists")
            continue

        await conn.execute(
            """
            INSERT INTO art_pieces
                (art_id, creator_user_id, current_owner_id, generation_tier,
                 canvas_width, canvas_height, model_name, model_version,
                 rendered_image_path, thumbnail_path, generation_hash,
                 seal_signature, is_tradeable, is_marketplace_listed)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
            """,
            art_id, creator_id, owner_id, tier, w, h, model, ver,
            img_path, thumb_path, gen_hash, seal, tradeable, listed,
        )
        print(f"  [created] Art piece {art_id}  (tier={tier}, creator={creator_id})")


async def seed_marketplace_listings(conn: asyncpg.Connection) -> None:
    """Insert active marketplace listings if they do not already exist."""
    listings = [
        (
            LISTING_IDS["listing1"],
            ART_IDS["art1"],
            USER_IDS["seller1"],
            1500,
            "active",
        ),
        (
            LISTING_IDS["listing2"],
            ART_IDS["art2"],
            USER_IDS["seller2"],
            3500,
            "active",
        ),
    ]

    for listing_id, art_id, seller_id, price, status in listings:
        exists = await conn.fetchval(
            "SELECT 1 FROM marketplace_listings WHERE listing_id = $1", listing_id
        )
        if exists:
            print(f"  [skip] Listing {listing_id} already exists")
            continue

        await conn.execute(
            """
            INSERT INTO marketplace_listings
                (listing_id, art_id, seller_user_id, asking_price_cents, status)
            VALUES ($1, $2, $3, $4, $5)
            """,
            listing_id, art_id, seller_id, price, status,
        )

        # Mark the art piece as marketplace-listed
        await conn.execute(
            "UPDATE art_pieces SET is_marketplace_listed = TRUE WHERE art_id = $1",
            art_id,
        )
        print(f"  [created] Listing {listing_id}  (art={art_id}, ${price / 100:.2f})")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    print(f"Connecting to: {DATABASE_URL}")
    conn = await asyncpg.connect(DATABASE_URL)

    try:
        print("\n--- Generation tiers ---")
        await seed_generation_tiers(conn)

        print("\n--- Credit packs ---")
        await seed_credit_packs(conn)

        print("\n--- Users ---")
        await seed_users(conn)

        print("\n--- Art pieces ---")
        await seed_art_pieces(conn)

        print("\n--- Marketplace listings ---")
        await seed_marketplace_listings(conn)

        print("\nStaging seed complete.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
