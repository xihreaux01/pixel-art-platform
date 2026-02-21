"""Art piece, generation job, and related models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.marketplace import MarketplaceListing, Transaction
    from app.models.user import User


class GenerationTierDefinition(Base):
    __tablename__ = "generation_tier_definitions"

    tier_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tier_name: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    canvas_width: Mapped[int] = mapped_column(Integer, nullable=False)
    canvas_height: Mapped[int] = mapped_column(Integer, nullable=False)
    credit_cost: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_budget_soft: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_budget_hard: Mapped[int] = mapped_column(Integer, nullable=False)
    job_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    allowed_tools: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)


class ArtPiece(Base):
    __tablename__ = "art_pieces"

    art_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    creator_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )
    current_owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )
    generation_tier: Mapped[str] = mapped_column(String(30), nullable=False)
    canvas_width: Mapped[int] = mapped_column(Integer, nullable=False)
    canvas_height: Mapped[int] = mapped_column(Integer, nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    model_version: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    rendered_image_path: Mapped[str] = mapped_column(String(500), nullable=False)
    thumbnail_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    generation_hash: Mapped[str] = mapped_column(
        String(128), unique=True, nullable=False
    )
    seal_signature: Mapped[str] = mapped_column(String(512), nullable=False)
    seal_key_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_tradeable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_marketplace_listed: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    times_traded: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    __table_args__ = (
        CheckConstraint("canvas_width > 0", name="ck_art_canvas_width_positive"),
        CheckConstraint("canvas_height > 0", name="ck_art_canvas_height_positive"),
    )

    creator: Mapped[User] = relationship(
        foreign_keys=[creator_user_id], back_populates="created_art_pieces"
    )
    current_owner: Mapped[User] = relationship(
        foreign_keys=[current_owner_id], back_populates="owned_art_pieces"
    )
    ownership_history: Mapped[list[OwnershipHistory]] = relationship(
        back_populates="art_piece", lazy="selectin"
    )
    generation_job: Mapped[Optional[ArtGenerationJob]] = relationship(
        back_populates="art_piece", lazy="selectin"
    )
    generation_summaries: Mapped[list[GenerationSummary]] = relationship(
        back_populates="art_piece", lazy="selectin"
    )
    marketplace_listings: Mapped[list[MarketplaceListing]] = relationship(
        back_populates="art_piece", lazy="selectin"
    )
    sale_transactions: Mapped[list[Transaction]] = relationship(
        back_populates="art_piece", lazy="selectin"
    )


class OwnershipHistory(Base):
    __tablename__ = "ownership_history"

    record_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    art_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("art_pieces.art_id"), nullable=False
    )
    from_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=True
    )
    to_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )
    transfer_type: Mapped[str] = mapped_column(String(20), nullable=False)
    transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    transferred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "transfer_type IN ('creation', 'trade', 'gift', 'admin_transfer')",
            name="ck_ownership_transfer_type",
        ),
    )

    art_piece: Mapped[ArtPiece] = relationship(back_populates="ownership_history")


class ArtGenerationJob(Base):
    __tablename__ = "art_generation_jobs"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )
    generation_tier: Mapped[str] = mapped_column(String(30), nullable=False)
    idempotency_key: Mapped[Optional[str]] = mapped_column(
        String(255), unique=True, nullable=True
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    art_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("art_pieces.art_id"), nullable=True
    )
    tool_calls_executed: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    checkpoint_canvas: Mapped[Optional[bytes]] = mapped_column(
        LargeBinary, nullable=True
    )
    checkpoint_tool_idx: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    last_checkpoint_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_progress_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    compensation_type: Mapped[Optional[str]] = mapped_column(
        String(30), nullable=True
    )
    compensation_amount: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'executing_tools', 'rendering', "
            "'completed', 'failed', 'cancelled')",
            name="ck_job_status",
        ),
    )

    user: Mapped[User] = relationship(back_populates="generation_jobs")
    art_piece: Mapped[Optional[ArtPiece]] = relationship(
        back_populates="generation_job"
    )
    generation_summaries: Mapped[list[GenerationSummary]] = relationship(
        back_populates="job", lazy="selectin"
    )
    tool_call_archives: Mapped[list[ToolCallArchive]] = relationship(
        back_populates="job", lazy="selectin"
    )


class GenerationSummary(Base):
    __tablename__ = "generation_summaries"

    summary_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("art_generation_jobs.job_id"), nullable=False
    )
    art_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("art_pieces.art_id"), nullable=True
    )
    total_tool_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_call_breakdown: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    first_tool_call_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_tool_call_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    generation_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    job: Mapped[ArtGenerationJob] = relationship(
        back_populates="generation_summaries"
    )
    art_piece: Mapped[Optional[ArtPiece]] = relationship(
        back_populates="generation_summaries"
    )


class ToolCallArchive(Base):
    __tablename__ = "tool_call_archives"

    archive_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("art_generation_jobs.job_id"), nullable=False
    )
    tool_calls_gz: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    toolcall_sequence_hash: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    job: Mapped[ArtGenerationJob] = relationship(back_populates="tool_call_archives")
