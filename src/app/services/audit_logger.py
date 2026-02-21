"""Structured JSON audit logger for financial and ownership events.

Emits structured log entries via structlog for trades, ownership transfers,
credit events, and moderation actions.  Every entry carries an ``audit: true``
flag so production log pipelines can filter on it easily.
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog


log = structlog.get_logger()


class AuditLogger:
    """Structured audit logger for platform events.

    All methods are synchronous -- they only emit log lines and perform
    no I/O beyond writing to the configured structlog sink.
    """

    # ------------------------------------------------------------------
    # Trade
    # ------------------------------------------------------------------

    def log_trade(self, trade_details: dict) -> None:
        """Log a marketplace trade.

        Expected keys in *trade_details*: ``buyer_user_id``,
        ``seller_user_id``, ``art_id``, ``amount``, ``fees``,
        ``transaction_id``.  Any extra keys are passed through.
        """
        log.info(
            "audit_event",
            event_type="trade",
            timestamp=datetime.now(timezone.utc).isoformat(),
            buyer_user_id=str(trade_details.get("buyer_user_id")),
            seller_user_id=str(trade_details.get("seller_user_id")),
            art_id=str(trade_details.get("art_id")),
            amount=trade_details.get("amount"),
            fees=trade_details.get("fees"),
            transaction_id=str(trade_details.get("transaction_id")),
            audit=True,
        )

    # ------------------------------------------------------------------
    # Ownership transfer
    # ------------------------------------------------------------------

    def log_ownership_transfer(
        self,
        art_id,
        from_user_id,
        to_user_id,
        transfer_type: str,
        transaction_id=None,
    ) -> None:
        """Record an ownership change (creation, trade, gift, admin)."""
        log.info(
            "audit_event",
            event_type="ownership_transfer",
            timestamp=datetime.now(timezone.utc).isoformat(),
            art_id=str(art_id),
            from_user_id=str(from_user_id) if from_user_id else None,
            to_user_id=str(to_user_id),
            transfer_type=transfer_type,
            transaction_id=str(transaction_id) if transaction_id else None,
            audit=True,
        )

    # ------------------------------------------------------------------
    # Credit event
    # ------------------------------------------------------------------

    def log_credit_event(
        self,
        user_id,
        amount,
        txn_type: str,
        reference_id=None,
    ) -> None:
        """Log a credit transaction (purchase, spend, refund, etc.)."""
        log.info(
            "audit_event",
            event_type="credit_event",
            timestamp=datetime.now(timezone.utc).isoformat(),
            user_id=str(user_id),
            amount=amount,
            txn_type=txn_type,
            reference_id=str(reference_id) if reference_id else None,
            audit=True,
        )

    # ------------------------------------------------------------------
    # Moderation event
    # ------------------------------------------------------------------

    def log_moderation_event(
        self,
        user_id,
        art_id,
        violation_type: str,
        action_taken: str,
    ) -> None:
        """Log a content moderation action."""
        log.info(
            "audit_event",
            event_type="moderation",
            timestamp=datetime.now(timezone.utc).isoformat(),
            user_id=str(user_id),
            art_id=str(art_id),
            violation_type=violation_type,
            action_taken=action_taken,
            audit=True,
        )
