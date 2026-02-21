"""Generation pipeline API endpoints.

Provides endpoints for creating, monitoring, cancelling, and querying
pixel art generation jobs.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.database import get_db
from app.integrations.ollama_client import OllamaClient
from app.models import User
from app.services.credit_service import atomic_deduct_credits, refund_credits
from app.services.free_tier_service import check_free_tier_eligibility, record_free_generation
from app.services.generation_orchestrator import GenerationOrchestrator

router = APIRouter(prefix="/api/v1/generations", tags=["generations"])


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class CreateGenerationRequest(BaseModel):
    tier: str = Field(..., min_length=1, max_length=30)
    prompt: str = Field(..., min_length=1, max_length=2000)
    idempotency_key: str | None = Field(None, max_length=255)


class CreateGenerationResponse(BaseModel):
    job_id: str
    status: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    art_id: str | None = None
    tool_calls_executed: int = 0
    error_message: str | None = None
    created_at: str | None = None
    completed_at: str | None = None


class CancelResponse(BaseModel):
    status: str


# ---------------------------------------------------------------------------
# Shared DB helpers
# ---------------------------------------------------------------------------

async def _get_tier_cost(db: AsyncSession, tier_name: str) -> int:
    """Look up the credit cost for a generation tier. Raises 400 if not found."""
    result = await db.execute(
        text("SELECT credit_cost FROM generation_tier_definitions WHERE tier_name = :tier_name"),
        {"tier_name": tier_name},
    )
    row = result.fetchone()
    if row is None:
        raise HTTPException(status_code=400, detail=f"Unknown tier: {tier_name}")
    return row[0]


async def _load_job_row(db: AsyncSession, job_id: uuid.UUID, user_id: uuid.UUID):
    """Load a job row and verify ownership. Returns the row or raises 404."""
    result = await db.execute(
        text(
            "SELECT job_id, user_id, generation_tier, status, art_id, "
            "tool_calls_executed, error_message, created_at, completed_at "
            "FROM art_generation_jobs WHERE job_id = :job_id"
        ),
        {"job_id": job_id},
    )
    row = result.fetchone()
    if row is None or row[1] != user_id:
        raise HTTPException(status_code=404, detail="Job not found")
    return row


# ---------------------------------------------------------------------------
# Background task runner
# ---------------------------------------------------------------------------

async def _run_generation(db: AsyncSession, redis, job_id: uuid.UUID, prompt: str) -> None:
    """Run the generation orchestrator in the background."""
    try:
        ollama = OllamaClient()
        orchestrator = GenerationOrchestrator(db=db, redis=redis, ollama=ollama)
        await orchestrator.run(job_id, prompt=prompt)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Create-job helpers (extracted for SRP)
# ---------------------------------------------------------------------------

async def _check_idempotency(db: AsyncSession, key: str, user_id: uuid.UUID):
    """Return an existing response if the idempotency key already exists, else None."""
    existing = await db.execute(
        text(
            "SELECT job_id, status FROM art_generation_jobs "
            "WHERE idempotency_key = :key AND user_id = :user_id"
        ),
        {"key": key, "user_id": user_id},
    )
    row = existing.fetchone()
    if row is not None:
        return CreateGenerationResponse(job_id=str(row[0]), status=row[1])
    return None


async def _insert_job_record(
    db: AsyncSession, job_id: uuid.UUID, user_id: uuid.UUID,
    tier: str, idempotency_key: str | None,
) -> None:
    """Insert a new art_generation_jobs row with status 'pending'."""
    now = datetime.now(timezone.utc)
    await db.execute(
        text(
            "INSERT INTO art_generation_jobs "
            "(job_id, user_id, generation_tier, idempotency_key, status, "
            "tool_calls_executed, checkpoint_tool_idx, created_at) "
            "VALUES (:job_id, :user_id, :tier, :idem_key, 'pending', 0, 0, :now)"
        ),
        {
            "job_id": job_id, "user_id": user_id,
            "tier": tier, "idem_key": idempotency_key, "now": now,
        },
    )


# ---------------------------------------------------------------------------
# SSE stream helper (extracted for SRP)
# ---------------------------------------------------------------------------

async def _sse_event_generator(redis, channel: str, request: Request):
    """Yield SSE-formatted events from Redis pub/sub until terminal or disconnect."""
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)
    try:
        while not await request.is_disconnected():
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message and message["type"] == "message":
                data = message["data"]
                yield f"data: {data}\n\n"
                if _is_terminal_event(data):
                    break
            else:
                yield ": keepalive\n\n"
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()


def _is_terminal_event(data: str) -> bool:
    """Return True if the SSE payload represents a terminal event."""
    try:
        parsed = json.loads(data)
        return parsed.get("event") in ("complete", "failed")
    except (json.JSONDecodeError, TypeError):
        return False


# ---------------------------------------------------------------------------
# POST /api/v1/generations
# ---------------------------------------------------------------------------

@router.post("", status_code=status.HTTP_201_CREATED, response_model=CreateGenerationResponse)
async def create_generation(
    body: CreateGenerationRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new generation job, deducting credits or using free tier."""
    is_free = body.tier == "free"

    if body.idempotency_key:
        existing = await _check_idempotency(db, body.idempotency_key, current_user.user_id)
        if existing is not None:
            return existing

    job_id = uuid.uuid4()

    if is_free:
        await check_free_tier_eligibility(db, current_user.user_id)
        await record_free_generation(db, current_user.user_id)
    else:
        credit_cost = await _get_tier_cost(db, body.tier)
        await atomic_deduct_credits(
            db=db, user_id=current_user.user_id,
            amount=credit_cost, txn_type="spend", reference_id=job_id,
        )

    await _insert_job_record(db, job_id, current_user.user_id, body.tier, body.idempotency_key)
    await db.commit()

    redis = request.app.state.redis
    asyncio.create_task(_run_generation(db, redis, job_id, body.prompt))

    return CreateGenerationResponse(job_id=str(job_id), status="pending")


# ---------------------------------------------------------------------------
# GET /api/v1/generations/{job_id}
# ---------------------------------------------------------------------------

@router.get("/{job_id}", response_model=JobStatusResponse)
async def get_job_status(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the current status of a generation job."""
    row = await _load_job_row(db, job_id, current_user.user_id)
    return JobStatusResponse(
        job_id=str(row[0]),
        status=row[3],
        art_id=str(row[4]) if row[4] else None,
        tool_calls_executed=row[5] or 0,
        error_message=row[6],
        created_at=str(row[7]) if row[7] else None,
        completed_at=str(row[8]) if row[8] else None,
    )


# ---------------------------------------------------------------------------
# DELETE /api/v1/generations/{job_id}
# ---------------------------------------------------------------------------

@router.delete("/{job_id}", response_model=CancelResponse)
async def cancel_generation(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Cancel a pending or in-progress generation job and refund credits."""
    row = await _load_job_row(db, job_id, current_user.user_id)
    job_status = row[3]

    if job_status not in ("pending", "executing_tools"):
        raise HTTPException(status_code=409, detail=f"Cannot cancel job with status: {job_status}")

    credit_cost = await _get_tier_cost(db, row[2])
    now = datetime.now(timezone.utc)
    await db.execute(
        text(
            "UPDATE art_generation_jobs SET status = 'cancelled', completed_at = :now "
            "WHERE job_id = :job_id"
        ),
        {"job_id": job_id, "now": now},
    )
    await refund_credits(db=db, user_id=current_user.user_id, amount=credit_cost, reference_id=job_id)
    await db.commit()

    return CancelResponse(status="cancelled")


# ---------------------------------------------------------------------------
# GET /api/v1/generations/{job_id}/events -- SSE stream
# ---------------------------------------------------------------------------

@router.get("/{job_id}/events")
async def generation_events(
    job_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Stream generation progress events via Server-Sent Events (SSE)."""
    result = await db.execute(
        text("SELECT user_id FROM art_generation_jobs WHERE job_id = :job_id"),
        {"job_id": job_id},
    )
    row = result.fetchone()
    if row is None or row[0] != current_user.user_id:
        raise HTTPException(status_code=404, detail="Job not found")

    redis = request.app.state.redis
    channel = f"generation:{job_id}"

    return StreamingResponse(
        _sse_event_generator(redis, channel, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
