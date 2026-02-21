"""User, credit, and authentication-related models."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.art import ArtGenerationJob, ArtPiece
    from app.models.marketplace import MarketplaceListing, PaymentRecord, Transaction


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    username: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    phone_hash: Mapped[Optional[str]] = mapped_column(
        String(64), unique=True, nullable=True
    )
    phone_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(
        String(255), unique=True, nullable=True
    )
    stripe_connect_id: Mapped[Optional[str]] = mapped_column(
        String(255), unique=True, nullable=True
    )
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    mfa_secret_enc: Mapped[Optional[bytes]] = mapped_column(
        LargeBinary, nullable=True
    )
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    credit_balance: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    free_generations_today: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    last_free_gen_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    content_violations_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, server_default="0"
    )

    # Relationships
    credit_transactions: Mapped[list[CreditTransaction]] = relationship(
        back_populates="user", lazy="selectin"
    )
    credit_balance_snapshots: Mapped[list[CreditBalanceSnapshot]] = relationship(
        back_populates="user", lazy="selectin"
    )
    created_art_pieces: Mapped[list[ArtPiece]] = relationship(
        foreign_keys="ArtPiece.creator_user_id",
        back_populates="creator",
        lazy="selectin",
    )
    owned_art_pieces: Mapped[list[ArtPiece]] = relationship(
        foreign_keys="ArtPiece.current_owner_id",
        back_populates="current_owner",
        lazy="selectin",
    )
    generation_jobs: Mapped[list[ArtGenerationJob]] = relationship(
        back_populates="user", lazy="selectin"
    )
    marketplace_listings: Mapped[list[MarketplaceListing]] = relationship(
        back_populates="seller", lazy="selectin"
    )
    buyer_transactions: Mapped[list[Transaction]] = relationship(
        foreign_keys="Transaction.buyer_user_id",
        back_populates="buyer",
        lazy="selectin",
    )
    seller_transactions: Mapped[list[Transaction]] = relationship(
        foreign_keys="Transaction.seller_user_id",
        back_populates="seller",
        lazy="selectin",
    )
    payment_records: Mapped[list[PaymentRecord]] = relationship(
        back_populates="user", lazy="selectin"
    )


class CreditPackDefinition(Base):
    __tablename__ = "credit_pack_definitions"

    pack_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    credit_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class CreditTransaction(Base):
    """Partitioned by created_at -- partition handled in migration SQL."""

    __tablename__ = "credit_transactions"

    txn_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    txn_type: Mapped[str] = mapped_column(String(30), nullable=False)
    reference_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False,
        primary_key=True,
    )

    __table_args__ = (
        CheckConstraint(
            "txn_type IN ('purchase', 'spend', 'refund', 'compensation', "
            "'admin_adjustment')",
            name="ck_credit_txn_type",
        ),
    )

    user: Mapped[User] = relationship(back_populates="credit_transactions")


class CreditBalanceSnapshot(Base):
    __tablename__ = "credit_balance_snapshots"

    snapshot_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )
    balance: Mapped[int] = mapped_column(Integer, nullable=False)
    as_of_txn_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("user_id", "as_of_txn_id", name="uq_snapshot_user_txn"),
    )

    user: Mapped[User] = relationship(back_populates="credit_balance_snapshots")
