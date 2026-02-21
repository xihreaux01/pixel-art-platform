"""Provenance chain API -- exposes the full ownership history of an art piece."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.database import get_db
from app.models import User


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class ProvenanceEntry(BaseModel):
    from_user_id: UUID | None
    to_user_id: UUID
    transfer_type: str
    transaction_id: UUID | None
    transferred_at: str  # ISO-8601 datetime string


class ProvenanceResponse(BaseModel):
    art_id: UUID
    creator_user_id: UUID
    generation_tier: str
    created_at: str  # ISO-8601 datetime string
    seal_signature: str
    provenance_chain: list[ProvenanceEntry]


# ---------------------------------------------------------------------------
# Data-access helpers
# ---------------------------------------------------------------------------

async def _fetch_art_metadata(db: AsyncSession, art_id: UUID):
    """Return the art_pieces row for *art_id*, or ``None``."""
    result = await db.execute(
        text(
            "SELECT art_id, creator_user_id, generation_tier, "
            "       created_at, seal_signature "
            "FROM art_pieces "
            "WHERE art_id = :art_id"
        ),
        {"art_id": art_id},
    )
    return result.fetchone()


async def _fetch_ownership_chain(db: AsyncSession, art_id: UUID) -> list[ProvenanceEntry]:
    """Return the chronological ownership chain for *art_id*."""
    result = await db.execute(
        text(
            "SELECT from_user_id, to_user_id, transfer_type, "
            "       transaction_id, transferred_at "
            "FROM ownership_history "
            "WHERE art_id = :art_id "
            "ORDER BY transferred_at ASC"
        ),
        {"art_id": art_id},
    )
    return [
        ProvenanceEntry(
            from_user_id=row[0],
            to_user_id=row[1],
            transfer_type=row[2],
            transaction_id=row[3],
            transferred_at=row[4].isoformat() if hasattr(row[4], "isoformat") else str(row[4]),
        )
        for row in result.fetchall()
    ]


def _build_response(art_row, chain: list[ProvenanceEntry]) -> ProvenanceResponse:
    """Assemble the final provenance response from DB data."""
    return ProvenanceResponse(
        art_id=art_row[0],
        creator_user_id=art_row[1],
        generation_tier=art_row[2],
        created_at=art_row[3].isoformat() if hasattr(art_row[3], "isoformat") else str(art_row[3]),
        seal_signature=art_row[4],
        provenance_chain=chain,
    )


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/v1/art", tags=["provenance"])


@router.get("/{art_id}/provenance", response_model=ProvenanceResponse)
async def get_provenance(
    art_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the complete provenance chain for an art piece."""
    art_row = await _fetch_art_metadata(db, art_id)
    if art_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Art piece not found",
        )

    chain = await _fetch_ownership_chain(db, art_id)
    return _build_response(art_row, chain)
