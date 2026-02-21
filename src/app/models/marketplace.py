"""Marketplace, transaction, payment, and infrastructure models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
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
    from app.models.art import ArtPiece
    from app.models.user import User


class MarketplaceListing(Base):
    __tablename__ = "marketplace_listings"

    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    art_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("art_pieces.art_id"), nullable=False
    )
    seller_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )
    asking_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency_code: Mapped[str] = mapped_column(
        String(3), default="USD", nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    listed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    sold_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint("asking_price_cents > 0", name="ck_listing_price_positive"),
        CheckConstraint(
            "status IN ('active', 'sold', 'cancelled')",
            name="ck_listing_status",
        ),
    )

    art_piece: Mapped[ArtPiece] = relationship(
        back_populates="marketplace_listings"
    )
    seller: Mapped[User] = relationship(back_populates="marketplace_listings")
    sale_transaction: Mapped[Optional[Transaction]] = relationship(
        back_populates="listing", lazy="selectin"
    )


class Transaction(Base):
    __tablename__ = "transactions"

    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    buyer_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )
    seller_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )
    art_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("art_pieces.art_id"), nullable=False
    )
    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("marketplace_listings.listing_id"),
        nullable=False,
    )
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    platform_fee_cents: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    seller_payout_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    stripe_payment_intent_id: Mapped[Optional[str]] = mapped_column(
        String(255), unique=True, nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    failure_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    initiated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint("amount_cents > 0", name="ck_txn_amount_positive"),
        CheckConstraint(
            "amount_cents = platform_fee_cents + seller_payout_cents",
            name="ck_txn_fee_split",
        ),
        CheckConstraint(
            "buyer_user_id != seller_user_id",
            name="ck_txn_no_self_trade",
        ),
        CheckConstraint(
            "status IN ('pending', 'completed', 'failed', 'refunded')",
            name="ck_txn_status",
        ),
    )

    buyer: Mapped[User] = relationship(
        foreign_keys=[buyer_user_id], back_populates="buyer_transactions"
    )
    seller: Mapped[User] = relationship(
        foreign_keys=[seller_user_id], back_populates="seller_transactions"
    )
    art_piece: Mapped[ArtPiece] = relationship(back_populates="sale_transactions")
    listing: Mapped[MarketplaceListing] = relationship(
        back_populates="sale_transaction"
    )


class PaymentRecord(Base):
    __tablename__ = "payment_records"

    payment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )
    payment_type: Mapped[str] = mapped_column(String(30), nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    stripe_payment_intent_id: Mapped[Optional[str]] = mapped_column(
        String(255), unique=True, nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    __table_args__ = (
        CheckConstraint("amount_cents >= 0", name="ck_payment_amount_nonneg"),
        CheckConstraint(
            "payment_type IN ('credit_purchase', 'marketplace_purchase', 'payout')",
            name="ck_payment_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'completed', 'failed', 'refunded')",
            name="ck_payment_status",
        ),
    )

    user: Mapped[User] = relationship(back_populates="payment_records")


class ProcessedWebhook(Base):
    __tablename__ = "processed_webhooks"

    event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )


class HmacKey(Base):
    __tablename__ = "hmac_keys"

    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    key_material: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    retired_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ToolPromptTemplate(Base):
    __tablename__ = "tool_prompt_templates"

    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tier: Mapped[str] = mapped_column(String(30), nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    tool_definitions: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
