#!/usr/bin/env python3
"""Nightly credit reconciliation script.

Compares each user's ``credit_balance`` column against the sum of their
``credit_transactions`` rows and reports any discrepancies.

Usage:
    DATABASE_URL=postgresql://... python scripts/reconcile_credits.py

Exit codes:
    0 -- all balances match
    1 -- one or more discrepancies found
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone

import asyncpg  # type: ignore[import-untyped]

DEFAULT_DATABASE_URL = "postgresql://app:devpassword@db:5432/pixelart"


def _get_dsn() -> str:
    """Return a raw ``postgresql://`` DSN (strip any SQLAlchemy dialect prefix)."""
    url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    # Normalise SQLAlchemy-style URLs that include +asyncpg / +psycopg2 etc.
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg2://"):
        if url.startswith(prefix):
            url = "postgresql://" + url[len(prefix):]
    return url


async def reconcile(dsn: str) -> list[dict]:
    """Run the reconciliation and return a list of discrepancy dicts."""
    conn: asyncpg.Connection = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            """
            SELECT
                u.user_id,
                u.credit_balance AS stored_balance,
                COALESCE(SUM(ct.amount), 0)::int AS computed_balance
            FROM users u
            LEFT JOIN credit_transactions ct USING (user_id)
            GROUP BY u.user_id, u.credit_balance
            HAVING u.credit_balance <> COALESCE(SUM(ct.amount), 0)
            ORDER BY u.user_id
            """
        )

        discrepancies: list[dict] = []
        for row in rows:
            discrepancies.append(
                {
                    "user_id": str(row["user_id"]),
                    "stored_balance": row["stored_balance"],
                    "computed_balance": row["computed_balance"],
                    "difference": row["stored_balance"] - row["computed_balance"],
                }
            )
        return discrepancies
    finally:
        await conn.close()


async def main() -> int:
    dsn = _get_dsn()
    discrepancies = await reconcile(dsn)

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_discrepancies": len(discrepancies),
        "discrepancies": discrepancies,
    }

    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")

    return 1 if discrepancies else 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
