"""Generation orchestrator -- state machine for art generation jobs.

State machine: PENDING -> EXECUTING_TOOLS -> SEALING -> COMPLETE | FAILED

Responsibilities:
1. Load the job from DB, verify status is PENDING, set to EXECUTING_TOOLS
2. Load the generation tier config (canvas dims, tools, budget, timeout)
3. Create ToolHarness with tier config
4. Call Ollama client's generate_pixel_art (injected via protocol)
5. Execute each tool call through the ToolHarness
6. Checkpoint canvas every 50 tool calls
7. On seal_canvas or budget exhaustion -> proceed to SEALING
8. Apply watermark, create HMAC seal
9. Save final PNG to /var/art/{art_id}.png (path from config)
10. Create ArtPiece record, GenerationSummary, ToolCallArchive
11. Set job status to COMPLETE
12. On any failure -> set job to FAILED + issue refund via credit_service
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from PIL import Image
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.ollama_client import GenerationConfig
from app.services.authenticity import AuthenticityManager, SealMetadata
from app.services.canvas_renderer import CanvasRenderer
from app.services.credit_service import refund_credits
from app.services.tool_harness import HarnessConfig, ToolCallResult, ToolHarness
from app.services.watermark import WatermarkEncoder

logger = logging.getLogger(__name__)

CHECKPOINT_INTERVAL = 50
ART_STORAGE_DIR = Path("/var/art")


# ---------------------------------------------------------------------------
# Protocol for the Ollama client (created in parallel by another agent)
# ---------------------------------------------------------------------------

class OllamaClientProtocol(Protocol):
    """Structural interface for the Ollama integration client."""

    async def generate_pixel_art(
        self,
        prompt: str,
        config: GenerationConfig,
    ) -> list[Any]: ...


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TierConfig:
    """Resolved generation tier configuration."""

    canvas_width: int
    canvas_height: int
    credit_cost: int
    tool_budget_soft: int
    tool_budget_hard: int
    job_timeout_seconds: int
    allowed_tools: list[str]


@dataclass
class JobContext:
    """Mutable state for a single generation job run."""

    job_id: uuid.UUID
    user_id: uuid.UUID
    tier_name: str
    tier: TierConfig
    prompt: str = ""
    art_id: uuid.UUID = field(default_factory=uuid.uuid4)
    tool_call_log: list[dict[str, Any]] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Redis progress publisher
# ---------------------------------------------------------------------------

class ProgressPublisher:
    """Publishes generation progress events via Redis pub/sub."""

    def __init__(self, redis: Any, job_id: uuid.UUID) -> None:
        self._redis = redis
        self._channel = f"generation:{job_id}"

    async def publish(self, status: str, tool_calls_executed: int, tool_budget: int) -> None:
        """Send a progress event to the SSE channel."""
        payload = json.dumps({
            "event": "progress",
            "tool_calls_executed": tool_calls_executed,
            "tool_budget": tool_budget,
            "status": status,
        })
        await self._redis.publish(self._channel, payload)

    async def publish_complete(self, art_id: str) -> None:
        """Send a completion event."""
        payload = json.dumps({"event": "complete", "art_id": art_id})
        await self._redis.publish(self._channel, payload)

    async def publish_failed(self, error: str) -> None:
        """Send a failure event."""
        payload = json.dumps({"event": "failed", "error": error})
        await self._redis.publish(self._channel, payload)


# ---------------------------------------------------------------------------
# Job DB helpers
# ---------------------------------------------------------------------------

class JobRepository:
    """Encapsulates database operations for generation jobs."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def load_job(self, job_id: uuid.UUID) -> dict[str, Any] | None:
        """Load a generation job row by ID."""
        result = await self._db.execute(
            text(
                "SELECT job_id, user_id, generation_tier, status "
                "FROM art_generation_jobs WHERE job_id = :job_id"
            ),
            {"job_id": job_id},
        )
        row = result.fetchone()
        if row is None:
            return None
        return {
            "job_id": row[0],
            "user_id": row[1],
            "generation_tier": row[2],
            "status": row[3],
        }

    async def load_tier(self, tier_name: str) -> TierConfig | None:
        """Load a tier definition by name."""
        result = await self._db.execute(
            text(
                "SELECT canvas_width, canvas_height, credit_cost, "
                "tool_budget_soft, tool_budget_hard, job_timeout_seconds, allowed_tools "
                "FROM generation_tier_definitions WHERE tier_name = :tier_name"
            ),
            {"tier_name": tier_name},
        )
        row = result.fetchone()
        if row is None:
            return None
        return TierConfig(
            canvas_width=row[0],
            canvas_height=row[1],
            credit_cost=row[2],
            tool_budget_soft=row[3],
            tool_budget_hard=row[4],
            job_timeout_seconds=row[5],
            allowed_tools=row[6] or [],
        )

    async def set_status(self, job_id: uuid.UUID, status: str) -> None:
        """Update the job status."""
        await self._db.execute(
            text("UPDATE art_generation_jobs SET status = :status WHERE job_id = :job_id"),
            {"job_id": job_id, "status": status},
        )

    async def set_failed(self, job_id: uuid.UUID, error_message: str) -> None:
        """Mark the job as failed with an error message."""
        now = datetime.now(timezone.utc)
        await self._db.execute(
            text(
                "UPDATE art_generation_jobs "
                "SET status = 'failed', error_message = :error, completed_at = :now "
                "WHERE job_id = :job_id"
            ),
            {"job_id": job_id, "error": error_message, "now": now},
        )

    async def save_checkpoint(self, job_id: uuid.UUID, canvas_data: bytes, tool_idx: int) -> None:
        """Persist a canvas checkpoint to the job row."""
        now = datetime.now(timezone.utc)
        await self._db.execute(
            text(
                "UPDATE art_generation_jobs "
                "SET checkpoint_canvas = :canvas, checkpoint_tool_idx = :idx, "
                "last_checkpoint_at = :now, tool_calls_executed = :idx "
                "WHERE job_id = :job_id"
            ),
            {"job_id": job_id, "canvas": canvas_data, "idx": tool_idx, "now": now},
        )

    async def save_art_piece(self, ctx: JobContext, seal_sig: str, gen_hash: str, paths: tuple[str, str]) -> None:
        """Insert the ArtPiece record.

        Free-tier art is explicitly non-tradeable (is_tradeable = false).
        Paid-tier art is tradeable (is_tradeable = true).
        """
        now = datetime.now(timezone.utc)
        tradeable = ctx.tier_name != "free"
        await self._db.execute(
            text(
                "INSERT INTO art_pieces "
                "(art_id, creator_user_id, current_owner_id, generation_tier, "
                "canvas_width, canvas_height, model_name, rendered_image_path, "
                "thumbnail_path, generation_hash, seal_signature, seal_key_version, "
                "is_tradeable, created_at) "
                "VALUES (:art_id, :user_id, :user_id, :tier, :w, :h, :model, "
                ":img_path, :thumb_path, :gen_hash, :seal, 1, :tradeable, :now)"
            ),
            {
                "art_id": ctx.art_id,
                "user_id": ctx.user_id,
                "tier": ctx.tier_name,
                "w": ctx.tier.canvas_width,
                "h": ctx.tier.canvas_height,
                "model": "ollama",
                "img_path": paths[0],
                "thumb_path": paths[1],
                "gen_hash": gen_hash,
                "seal": seal_sig,
                "tradeable": tradeable,
                "now": now,
            },
        )

    async def save_summary(self, ctx: JobContext, duration_ms: int) -> None:
        """Insert a GenerationSummary record."""
        now = datetime.now(timezone.utc)
        breakdown: dict[str, int] = {}
        for entry in ctx.tool_call_log:
            name = entry.get("tool_name", "unknown")
            breakdown[name] = breakdown.get(name, 0) + 1

        await self._db.execute(
            text(
                "INSERT INTO generation_summaries "
                "(job_id, art_id, total_tool_calls, tool_call_breakdown, "
                "first_tool_call_at, last_tool_call_at, generation_duration_ms, created_at) "
                "VALUES (:job_id, :art_id, :total, :breakdown, :first, :last, :dur, :now)"
            ),
            {
                "job_id": ctx.job_id,
                "art_id": ctx.art_id,
                "total": len(ctx.tool_call_log),
                "breakdown": json.dumps(breakdown),
                "first": ctx.started_at,
                "last": now,
                "dur": duration_ms,
                "now": now,
            },
        )

    async def save_tool_archive(self, ctx: JobContext) -> None:
        """Insert a ToolCallArchive record (gzip-compressed JSON)."""
        now = datetime.now(timezone.utc)
        raw = json.dumps(ctx.tool_call_log).encode()
        compressed = gzip.compress(raw)
        seq_hash = hashlib.sha256(raw).hexdigest()

        await self._db.execute(
            text(
                "INSERT INTO tool_call_archives "
                "(job_id, tool_calls_gz, toolcall_sequence_hash, created_at) "
                "VALUES (:job_id, :gz, :hash, :now)"
            ),
            {
                "job_id": ctx.job_id,
                "gz": compressed,
                "hash": seq_hash,
                "now": now,
            },
        )

    async def complete_job(self, job_id: uuid.UUID, art_id: uuid.UUID) -> None:
        """Mark the job as completed."""
        now = datetime.now(timezone.utc)
        await self._db.execute(
            text(
                "UPDATE art_generation_jobs "
                "SET status = 'completed', art_id = :art_id, completed_at = :now "
                "WHERE job_id = :job_id"
            ),
            {"job_id": job_id, "art_id": art_id, "now": now},
        )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class GenerationOrchestrator:
    """Main state machine for art generation jobs.

    Coordinates the full generation flow: tool execution, checkpointing,
    watermarking, HMAC sealing, and record creation.
    """

    def __init__(
        self,
        db: AsyncSession,
        redis: Any,
        ollama: OllamaClientProtocol,
    ) -> None:
        self._db = db
        self._redis = redis
        self._ollama = ollama
        self._repo = JobRepository(db)

    async def run(self, job_id: uuid.UUID, prompt: str = "") -> None:
        """Execute the full generation pipeline for *job_id*."""
        ctx: JobContext | None = None
        try:
            ctx = await self._initialise(job_id)
            if prompt:
                ctx.prompt = prompt
            await self._execute_tools(ctx)
            await self._seal_and_persist(ctx)
        except Exception as exc:
            logger.exception("Generation failed for job %s", job_id)
            await self._handle_failure(job_id, ctx, str(exc))

    # ------------------------------------------------------------------
    # Phase 1 -- initialise
    # ------------------------------------------------------------------

    async def _initialise(self, job_id: uuid.UUID) -> JobContext:
        """Load job and tier, validate PENDING status, transition to EXECUTING_TOOLS."""
        job = await self._repo.load_job(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        if job["status"] != "pending":
            raise ValueError(f"Job {job_id} has unexpected status: {job['status']}")

        tier = await self._repo.load_tier(job["generation_tier"])
        if tier is None:
            raise ValueError(f"Tier {job['generation_tier']!r} not found")

        await self._repo.set_status(job_id, "executing_tools")
        await self._db.commit()

        return JobContext(
            job_id=job_id,
            user_id=job["user_id"],
            tier_name=job["generation_tier"],
            tier=tier,
        )

    # ------------------------------------------------------------------
    # Phase 2 -- execute tool calls
    # ------------------------------------------------------------------

    async def _execute_tools(self, ctx: JobContext) -> None:
        """Run the LLM tool-calling loop and execute results via ToolHarness."""
        harness_config = HarnessConfig(
            canvas_width=ctx.tier.canvas_width,
            canvas_height=ctx.tier.canvas_height,
            allowed_tools=ctx.tier.allowed_tools,
            tool_budget_hard=ctx.tier.tool_budget_hard,
        )
        harness = ToolHarness(harness_config)
        publisher = ProgressPublisher(self._redis, ctx.job_id)
        renderer = CanvasRenderer(ctx.tier.canvas_width, ctx.tier.canvas_height)

        gen_config = GenerationConfig(
            canvas_width=ctx.tier.canvas_width,
            canvas_height=ctx.tier.canvas_height,
            allowed_tools=ctx.tier.allowed_tools,
            max_iterations=20,
        )
        tool_calls = await self._ollama.generate_pixel_art(
            prompt=ctx.prompt,
            config=gen_config,
        )

        for call in tool_calls:
            await self._process_tool_call(call, harness, renderer, publisher, ctx)
            if harness.sealed:
                break

        # Transfer final canvas state to renderer
        renderer.set_canvas(harness.canvas)
        ctx._renderer = renderer  # type: ignore[attr-defined]

    async def _process_tool_call(
        self,
        call: Any,
        harness: ToolHarness,
        renderer: CanvasRenderer,
        publisher: ProgressPublisher,
        ctx: JobContext,
    ) -> None:
        """Execute a single tool call, log it, publish progress, and checkpoint if needed."""
        tool_name = call.get("tool_name", "") if isinstance(call, dict) else getattr(call, "name", "")
        raw_args = call.get("arguments", {}) if isinstance(call, dict) else getattr(call, "arguments", {})

        result: ToolCallResult = harness.execute(tool_name, raw_args)
        ctx.tool_call_log.append({
            "tool_name": tool_name,
            "arguments": raw_args,
            "success": result.success,
            "message": result.message,
        })

        await publisher.publish(
            status="executing_tools",
            tool_calls_executed=harness.tool_calls_executed,
            tool_budget=ctx.tier.tool_budget_hard,
        )

        # Checkpoint every CHECKPOINT_INTERVAL successful calls
        if harness.tool_calls_executed > 0 and harness.tool_calls_executed % CHECKPOINT_INTERVAL == 0:
            renderer.set_canvas(harness.canvas)
            checkpoint_data = renderer.checkpoint()
            await self._repo.save_checkpoint(ctx.job_id, checkpoint_data, harness.tool_calls_executed)
            await self._db.commit()

    # ------------------------------------------------------------------
    # Phase 3 -- seal, watermark, persist
    # ------------------------------------------------------------------

    async def _seal_and_persist(self, ctx: JobContext) -> None:
        """Apply watermark, create HMAC seal, save files and DB records."""
        await self._repo.set_status(ctx.job_id, "rendering")
        await self._db.commit()

        renderer: CanvasRenderer = ctx._renderer  # type: ignore[attr-defined]
        publisher = ProgressPublisher(self._redis, ctx.job_id)

        # Watermark
        watermarked = WatermarkEncoder.encode(renderer.get_canvas(), ctx.art_id, ctx.user_id)
        renderer.set_canvas(watermarked)

        # Export image bytes
        image_bytes = renderer.to_png_bytes()
        thumbnail_bytes = renderer.create_thumbnail()

        # HMAC seal
        metadata = SealMetadata(
            art_id=str(ctx.art_id),
            creator_id=str(ctx.user_id),
            model_name="ollama",
        )
        seal_sig, gen_hash = AuthenticityManager.create_seal(image_bytes, metadata)

        # Write files
        ART_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        img_path = ART_STORAGE_DIR / f"{ctx.art_id}.png"
        thumb_path = ART_STORAGE_DIR / f"{ctx.art_id}_thumb.png"
        img_path.write_bytes(image_bytes)
        thumb_path.write_bytes(thumbnail_bytes)

        # Persist records
        await self._repo.save_art_piece(ctx, seal_sig, gen_hash, (str(img_path), str(thumb_path)))
        duration_ms = int((datetime.now(timezone.utc) - ctx.started_at).total_seconds() * 1000)
        await self._repo.save_summary(ctx, duration_ms)
        await self._repo.save_tool_archive(ctx)
        await self._repo.complete_job(ctx.job_id, ctx.art_id)
        await self._db.commit()

        await publisher.publish_complete(str(ctx.art_id))

    # ------------------------------------------------------------------
    # Failure handling
    # ------------------------------------------------------------------

    async def _handle_failure(self, job_id: uuid.UUID, ctx: JobContext | None, error: str) -> None:
        """Mark job FAILED and issue a credit refund."""
        try:
            await self._repo.set_failed(job_id, error)
            if ctx is not None:
                await refund_credits(self._db, ctx.user_id, ctx.tier.credit_cost, reference_id=ctx.job_id)
            await self._db.commit()

            publisher = ProgressPublisher(self._redis, job_id)
            await publisher.publish_failed(error)
        except Exception:
            logger.exception("Failed to handle failure for job %s", job_id)
