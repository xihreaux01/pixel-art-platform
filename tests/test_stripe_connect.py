"""Tests for the Stripe Connect integration.

All Stripe API calls are mocked -- no real Stripe account is required.

Run with:
    ./venv/bin/python -m pytest tests/test_stripe_connect.py -v
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.integrations.stripe_connect import StripeConnectService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_account(
    account_id: str = "acct_test_123",
    charges_enabled: bool = True,
    payouts_enabled: bool = True,
    details_submitted: bool = True,
) -> MagicMock:
    """Build a fake stripe.Account object."""
    account = MagicMock()
    account.id = account_id
    account.charges_enabled = charges_enabled
    account.payouts_enabled = payouts_enabled
    account.details_submitted = details_submitted
    return account


def _mock_account_link(url: str = "https://connect.stripe.com/setup/e/acct_test_123") -> MagicMock:
    """Build a fake stripe.AccountLink object."""
    link = MagicMock()
    link.url = url
    return link


def _mock_transfer(transfer_id: str = "tr_test_456") -> MagicMock:
    """Build a fake stripe.Transfer object."""
    transfer = MagicMock()
    transfer.id = transfer_id
    return transfer


# ---------------------------------------------------------------------------
# 1. test_create_connect_account
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_connect_account():
    """Creating a Connect account returns account_id and onboarding_url."""
    fake_account = _mock_account(account_id="acct_seller_001")
    fake_link = _mock_account_link(url="https://connect.stripe.com/onboarding/acct_seller_001")

    with (
        patch("stripe.Account.create", return_value=fake_account) as mock_create,
        patch("stripe.AccountLink.create", return_value=fake_link) as mock_link,
    ):
        service = StripeConnectService()
        result = await service.create_connect_account(
            user_id="u_42",
            email="artist@example.com",
        )

    # Verify stripe.Account.create was called with the right args
    mock_create.assert_called_once_with(
        type="express",
        email="artist@example.com",
        metadata={"user_id": "u_42"},
    )

    # Verify stripe.AccountLink.create received the new account id
    mock_link.assert_called_once_with(
        account="acct_seller_001",
        refresh_url="https://localhost/seller/onboarding/refresh",
        return_url="https://localhost/seller/onboarding/complete",
        type="account_onboarding",
    )

    assert result["account_id"] == "acct_seller_001"
    assert result["onboarding_url"] == "https://connect.stripe.com/onboarding/acct_seller_001"


# ---------------------------------------------------------------------------
# 2. test_create_transfer
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_transfer():
    """Transferring funds returns the transfer_id and amount."""
    fake_transfer = _mock_transfer(transfer_id="tr_payout_789")

    with patch("stripe.Transfer.create", return_value=fake_transfer) as mock_xfer:
        service = StripeConnectService()
        result = await service.create_transfer(
            connect_account_id="acct_seller_001",
            amount_cents=9000,
            description="Sale of pixel-dragon #42",
        )

    mock_xfer.assert_called_once_with(
        amount=9000,
        currency="usd",
        destination="acct_seller_001",
        description="Sale of pixel-dragon #42",
    )

    assert result["transfer_id"] == "tr_payout_789"
    assert result["amount"] == 9000


# ---------------------------------------------------------------------------
# 3. test_get_account_status
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_account_status():
    """Retrieving account status returns the expected fields."""
    fake_account = _mock_account(
        account_id="acct_seller_001",
        charges_enabled=True,
        payouts_enabled=False,
        details_submitted=True,
    )

    with patch("stripe.Account.retrieve", return_value=fake_account) as mock_retrieve:
        service = StripeConnectService()
        result = await service.get_account_status("acct_seller_001")

    mock_retrieve.assert_called_once_with("acct_seller_001")

    assert result["account_id"] == "acct_seller_001"
    assert result["charges_enabled"] is True
    assert result["payouts_enabled"] is False
    assert result["details_submitted"] is True


# ---------------------------------------------------------------------------
# 4. test_calculate_seller_payout_10_percent_fee
# ---------------------------------------------------------------------------

def test_calculate_seller_payout_10_percent_fee():
    """$100 sale (10000 cents) -> $90 seller, $10 platform fee."""
    service = StripeConnectService()
    seller_amount, platform_fee = service.calculate_seller_payout(10000)

    assert platform_fee == 1000  # 10 %
    assert seller_amount == 9000
    assert seller_amount + platform_fee == 10000


# ---------------------------------------------------------------------------
# 5. test_calculate_seller_payout_rounding
# ---------------------------------------------------------------------------

def test_calculate_seller_payout_rounding():
    """$1 sale (100 cents) -> 90 cents seller, 10 cents platform fee.

    Also verifies integer division handles non-round amounts correctly:
    e.g. 99 cents -> platform_fee = 99 // 10 = 9, seller = 90.
    """
    service = StripeConnectService()

    # Exact division
    seller_amount, platform_fee = service.calculate_seller_payout(100)
    assert platform_fee == 10
    assert seller_amount == 90
    assert seller_amount + platform_fee == 100

    # Non-round amount: 99 cents
    seller_amount, platform_fee = service.calculate_seller_payout(99)
    assert platform_fee == 9  # 99 // 10 = 9
    assert seller_amount == 90  # 99 - 9 = 90
    assert seller_amount + platform_fee == 99

    # Edge case: 1 cent
    seller_amount, platform_fee = service.calculate_seller_payout(1)
    assert platform_fee == 0  # 1 // 10 = 0
    assert seller_amount == 1
    assert seller_amount + platform_fee == 1
