#!/usr/bin/env python3
"""Weekly cleanup of old tool_call_archives rows.

Applies differentiated retention policies per the architecture plan:
  - Successful generations: 90 days
  - Failed generations:     30 days
  - Flagged (moderation):   365 days (1 year)

Deletes rows in batches to avoid long-held locks.

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

# Retention policies (days) keyed by job status category.
RETENTION_DAYS_SUCCESS = 90
RETENTION_DAYS_FAILED = 30
RETENTION_DAYS_FLAGGED = 365

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


async def _batch_delete(conn: asyncpg.Connection, category: str, cutoff: datetime) -> int:
    """Delete archives for a given status category older than *cutoff*.

    The query joins tool_call_archives to art_generation_jobs to determine the
    job's final status, then applies the per-category retention cutoff.

    Returns total rows deleted for this category.
    """
    total_deleted = 0

    # Map category to the set of job statuses it covers.
    status_sets = {
        "success": ("complete",),
        "failed": ("failed", "cancelled"),
        "flagged": ("flagged",),
    }
    statuses = status_sets.get(category, ())
    if not statuses:
        return 0

    while True:
        result: str = await conn.execute(
            """
            DELETE FROM tool_call_archives
            WHERE archive_id IN (
                SELECT tca.archive_id
                FROM tool_call_archives tca
                JOIN art_generation_jobs agj ON agj.job_id = tca.job_id
                WHERE agj.status = ANY($1::text[])
                  AND tca.created_at < $2
                LIMIT $3
            )
            """,
            list(statuses),
            cutoff,
            BATCH_SIZE,
        )
        deleted = int(result.split()[-1])
        total_deleted += deleted
        if deleted > 0:
            log.info("[%s] Batch deleted %d rows (running total: %d)", category, deleted, total_deleted)
        if deleted < BATCH_SIZE:
            break

    return total_deleted


async def cleanup(dsn: str) -> int:
    """Delete stale archives per retention policy and return total rows deleted."""
    conn: asyncpg.Connection = await asyncpg.connect(dsn)
    now = datetime.now(timezone.utc)
    total_deleted = 0

    try:
        for category, retention_days in [
            ("success", RETENTION_DAYS_SUCCESS),
            ("failed", RETENTION_DAYS_FAILED),
            ("flagged", RETENTION_DAYS_FLAGGED),
        ]:
            cutoff = now - timedelta(days=retention_days)
            log.info("Cleaning %s archives older than %d days (cutoff: %s)", category, retention_days, cutoff.isoformat())
            deleted = await _batch_delete(conn, category, cutoff)
            total_deleted += deleted
            log.info("[%s] Subtotal deleted: %d", category, deleted)
    finally:
        await conn.close()

    return total_deleted


async def main() -> None:
    dsn = _get_dsn()
    log.info(
        "Starting cleanup -- retention: success=%dd, failed=%dd, flagged=%dd",
        RETENTION_DAYS_SUCCESS,
        RETENTION_DAYS_FAILED,
        RETENTION_DAYS_FLAGGED,
    )
    total = await cleanup(dsn)
    log.info("Cleanup complete. Total rows deleted: %d", total)


if __name__ == "__main__":
    asyncio.run(main())
    sys.exit(0)
