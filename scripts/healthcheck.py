#!/usr/bin/env python3
"""Monitoring / healthcheck script for the Pixel Art Platform.

Checks the availability of:
    - FastAPI application (/health)
    - PostgreSQL
    - Redis
    - Ollama (/api/version)

Outputs a JSON array of ``{service, status, latency_ms}`` objects.

Exit codes:
    0 -- all services healthy
    1 -- one or more services unhealthy
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from typing import Any

import asyncpg  # type: ignore[import-untyped]
import httpx
from redis.asyncio import Redis

# ---------------------------------------------------------------------------
# Configuration -- all overridable via environment variables
# ---------------------------------------------------------------------------

APP_URL = os.environ.get("APP_URL", "http://localhost:8000")
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://app:devpassword@db:5432/pixelart"
)
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434")

# Timeout in seconds for each individual check.
CHECK_TIMEOUT = float(os.environ.get("HEALTHCHECK_TIMEOUT", "5"))


def _pg_dsn(url: str) -> str:
    """Normalise a SQLAlchemy-style URL to a plain ``postgresql://`` DSN."""
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg2://"):
        if url.startswith(prefix):
            url = "postgresql://" + url[len(prefix):]
    return url


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

async def check_app(client: httpx.AsyncClient) -> dict[str, Any]:
    """Hit the FastAPI /health endpoint."""
    start = time.monotonic()
    try:
        resp = await client.get(f"{APP_URL}/health", timeout=CHECK_TIMEOUT)
        latency = (time.monotonic() - start) * 1000
        healthy = resp.status_code == 200
        return {
            "service": "app",
            "status": "healthy" if healthy else "unhealthy",
            "latency_ms": round(latency, 2),
        }
    except Exception as exc:
        latency = (time.monotonic() - start) * 1000
        return {
            "service": "app",
            "status": "unhealthy",
            "latency_ms": round(latency, 2),
            "error": str(exc),
        }


async def check_postgres() -> dict[str, Any]:
    """Open a connection and run ``SELECT 1``."""
    dsn = _pg_dsn(DATABASE_URL)
    start = time.monotonic()
    try:
        conn = await asyncio.wait_for(
            asyncpg.connect(dsn), timeout=CHECK_TIMEOUT
        )
        try:
            await conn.fetchval("SELECT 1")
        finally:
            await conn.close()
        latency = (time.monotonic() - start) * 1000
        return {
            "service": "postgres",
            "status": "healthy",
            "latency_ms": round(latency, 2),
        }
    except Exception as exc:
        latency = (time.monotonic() - start) * 1000
        return {
            "service": "postgres",
            "status": "unhealthy",
            "latency_ms": round(latency, 2),
            "error": str(exc),
        }


async def check_redis() -> dict[str, Any]:
    """PING the Redis server."""
    start = time.monotonic()
    try:
        redis = Redis.from_url(REDIS_URL, decode_responses=True)
        try:
            pong = await asyncio.wait_for(redis.ping(), timeout=CHECK_TIMEOUT)
            healthy = bool(pong)
        finally:
            await redis.close()
        latency = (time.monotonic() - start) * 1000
        return {
            "service": "redis",
            "status": "healthy" if healthy else "unhealthy",
            "latency_ms": round(latency, 2),
        }
    except Exception as exc:
        latency = (time.monotonic() - start) * 1000
        return {
            "service": "redis",
            "status": "unhealthy",
            "latency_ms": round(latency, 2),
            "error": str(exc),
        }


async def check_ollama(client: httpx.AsyncClient) -> dict[str, Any]:
    """Hit the Ollama /api/version endpoint."""
    start = time.monotonic()
    try:
        resp = await client.get(
            f"{OLLAMA_URL}/api/version", timeout=CHECK_TIMEOUT
        )
        latency = (time.monotonic() - start) * 1000
        healthy = resp.status_code == 200
        return {
            "service": "ollama",
            "status": "healthy" if healthy else "unhealthy",
            "latency_ms": round(latency, 2),
        }
    except Exception as exc:
        latency = (time.monotonic() - start) * 1000
        return {
            "service": "ollama",
            "status": "unhealthy",
            "latency_ms": round(latency, 2),
            "error": str(exc),
        }


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

async def run_checks() -> list[dict[str, Any]]:
    """Run all health checks concurrently and return results."""
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            check_app(client),
            check_postgres(),
            check_redis(),
            check_ollama(client),
        )
    return list(results)


async def main() -> int:
    results = await run_checks()

    json.dump(results, sys.stdout, indent=2)
    sys.stdout.write("\n")

    all_healthy = all(r["status"] == "healthy" for r in results)
    return 0 if all_healthy else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
