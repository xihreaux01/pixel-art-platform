"""Stripe Connect integration for marketplace seller payouts.

Handles seller onboarding via Stripe Connect Express accounts and
manages transfers (payouts) when marketplace sales occur. The platform
retains a 10 % fee on every transaction.

Usage:
    from app.integrations.stripe_connect import StripeConnectService

    service = StripeConnectService()
    result = await service.create_connect_account(user_id="u_123", email="seller@example.com")
"""

from __future__ import annotations

import stripe

from app.config import settings


class StripeConnectService:
    """Manages Stripe Connect accounts for marketplace sellers."""

    def __init__(self) -> None:
        stripe.api_key = settings.STRIPE_SECRET_KEY

    # ------------------------------------------------------------------
    # Account lifecycle
    # ------------------------------------------------------------------

    async def create_connect_account(
        self,
        user_id: str,
        email: str,
    ) -> dict:
        """Create a Stripe Connect Express account for a seller.

        Returns dict with account_id and onboarding_url.
        """
        account = stripe.Account.create(
            type="express",
            email=email,
            metadata={"user_id": user_id},
        )

        # Create onboarding link
        link = stripe.AccountLink.create(
            account=account.id,
            refresh_url="https://localhost/seller/onboarding/refresh",
            return_url="https://localhost/seller/onboarding/complete",
            type="account_onboarding",
        )

        return {
            "account_id": account.id,
            "onboarding_url": link.url,
        }

    async def get_account_status(self, connect_account_id: str) -> dict:
        """Check if a Connect account is fully onboarded."""
        account = stripe.Account.retrieve(connect_account_id)
        return {
            "account_id": account.id,
            "charges_enabled": account.charges_enabled,
            "payouts_enabled": account.payouts_enabled,
            "details_submitted": account.details_submitted,
        }

    # ------------------------------------------------------------------
    # Transfers / payouts
    # ------------------------------------------------------------------

    async def create_transfer(
        self,
        connect_account_id: str,
        amount_cents: int,
        description: str,
    ) -> dict:
        """Transfer funds to a seller's Connect account.

        The amount should already have the platform fee deducted.
        """
        transfer = stripe.Transfer.create(
            amount=amount_cents,
            currency="usd",
            destination=connect_account_id,
            description=description,
        )
        return {"transfer_id": transfer.id, "amount": amount_cents}

    # ------------------------------------------------------------------
    # Fee calculation
    # ------------------------------------------------------------------

    def calculate_seller_payout(
        self,
        sale_price_cents: int,
    ) -> tuple[int, int]:
        """Calculate seller payout and platform fee.

        Platform takes 10 % fee.
        Returns (seller_amount, platform_fee).
        """
        platform_fee = sale_price_cents // 10  # 10 % fee
        seller_amount = sale_price_cents - platform_fee
        return seller_amount, platform_fee
