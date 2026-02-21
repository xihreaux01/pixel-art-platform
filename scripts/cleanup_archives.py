#!/usr/bin/env python3
"""Weekly cleanup of old tool_call_archives rows.

Deletes rows older than 30 days in batches to avoid long-held locks.

Usage:
    DATABASE_URL=postgresql://... python scripts/cleanup_archives.py

Exit codes:
    0 -- always (informational-only script)
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import asyncpg  # type: ignore[import-untyped]

DEFAULT_DATABASE_URL = "postgresql://app:devpassword@db:5432/pixelart"
BATCH_SIZE = 10_000
RETENTION_DAYS = 30

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


def _get_dsn() -> str:
    """Return a raw ``postgresql://`` DSN (strip any SQLAlchemy dialect prefix)."""
    url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg2://"):
        if url.startswith(prefix):
            url = "postgresql://" + url[len(prefix):]
    return url


async def cleanup(dsn: str) -> int:
    """Delete stale archives in batches and return total rows deleted."""
    conn: asyncpg.Connection = await asyncpg.connect(dsn)
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    total_deleted = 0

    try:
        while True:
            # Use a CTE with LIMIT to batch-delete without locking the whole table.
            result: str = await conn.execute(
                """
                DELETE FROM tool_call_archives
                WHERE archive_id IN (
                    SELECT archive_id
                    FROM tool_call_archives
                    WHERE created_at < $1
                    LIMIT $2
                )
                """,
                cutoff,
                BATCH_SIZE,
            )
            # asyncpg returns e.g. "DELETE 5000"
            deleted = int(result.split()[-1])
            total_deleted += deleted
            log.info("Batch deleted %d rows (running total: %d)", deleted, total_deleted)

            if deleted < BATCH_SIZE:
                break
    finally:
        await conn.close()

    return total_deleted


async def main() -> None:
    dsn = _get_dsn()
    log.info("Starting cleanup of tool_call_archives older than %d days", RETENTION_DAYS)
    total = await cleanup(dsn)
    log.info("Cleanup complete. Total rows deleted: %d", total)


if __name__ == "__main__":
    asyncio.run(main())
    sys.exit(0)
