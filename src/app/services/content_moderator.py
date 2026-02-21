"""Post-generation content moderation scanner.

Scans generated pixel art for policy violations (NSFW, violence, hate symbols).
Flagged content is quarantined, the user receives a full credit refund, and
three violations result in account suspension.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from PIL import Image
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.credit_service import refund_credits


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ModerationResult:
    """Outcome of a content scan."""

    is_approved: bool
    violation_type: str | None = None  # e.g., "nsfw", "violence", "hate_symbol"
    confidence: float = 0.0


# ---------------------------------------------------------------------------
# Moderation service
# ---------------------------------------------------------------------------

SUSPENSION_THRESHOLD = 3


class ContentModerator:
    """Post-generation content moderation scanner.

    In production this would call Claude Vision API or AWS Rekognition.
    For now, implements a stub that always approves, with infrastructure
    for adding real moderation later.
    """

    # ------------------------------------------------------------------
    # Scan
    # ------------------------------------------------------------------

    async def scan(self, image: Image.Image) -> ModerationResult:
        """Scan an image for policy violations.

        Returns *ModerationResult* with ``is_approved`` and violation details.

        The current implementation is a stub that always approves.
        """
        # Stub: always approve
        # TODO: integrate with Claude Vision API or AWS Rekognition
        return ModerationResult(is_approved=True)

    # ------------------------------------------------------------------
    # Violation bookkeeping
    # ------------------------------------------------------------------

    async def record_violation(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        art_id: uuid.UUID,
        violation_type: str,
    ) -> int:
        """Record a content violation for a user.

        Increments ``content_violations_count`` on the ``users`` row
        (uses ``COALESCE`` so the column can default to ``NULL`` /
        ``0``).

        Returns the user's **total** violation count after incrementing.
        If the count reaches ``SUSPENSION_THRESHOLD`` (3), the account
        is suspended (``is_active`` set to ``FALSE``).
        """
        # Atomically increment count and return the new value.
        result = await db.execute(
            text(
                "UPDATE users "
                "SET content_violations_count = COALESCE(content_violations_count, 0) + 1 "
                "WHERE user_id = :user_id "
                "RETURNING content_violations_count"
            ),
            {"user_id": user_id},
        )
        row = result.fetchone()
        if row is None:
            raise ValueError(f"User {user_id} not found")

        new_count: int = row[0]

        # Suspend account on reaching the threshold.
        if new_count >= SUSPENSION_THRESHOLD:
            await db.execute(
                text(
                    "UPDATE users SET is_active = FALSE WHERE user_id = :user_id"
                ),
                {"user_id": user_id},
            )

        return new_count

    # ------------------------------------------------------------------
    # Full violation handler
    # ------------------------------------------------------------------

    async def handle_violation(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        job_id: uuid.UUID,
        violation_type: str,
        credit_cost: int,
    ) -> None:
        """Full violation workflow: quarantine, refund, record, suspend.

        1. Mark the generation job as ``failed`` with an error message.
        2. Issue a full credit refund via :func:`refund_credits`.
        3. Record the violation (may suspend on 3rd strike).
        """
        # 1. Quarantine -- mark the job as failed so the image is never
        #    delivered to the user.
        await db.execute(
            text(
                "UPDATE art_generation_jobs "
                "SET status = 'failed', "
                "    error_message = :error_msg "
                "WHERE job_id = :job_id"
            ),
            {
                "job_id": job_id,
                "error_msg": f"Content violation: {violation_type}",
            },
        )

        # 2. Full credit refund.
        await refund_credits(
            db=db,
            user_id=user_id,
            amount=credit_cost,
            reference_id=job_id,
        )

        # 3. Record the violation (and auto-suspend at threshold).
        await self.record_violation(
            db=db,
            user_id=user_id,
            art_id=job_id,  # use job_id as reference when art wasn't persisted
            violation_type=violation_type,
        )
