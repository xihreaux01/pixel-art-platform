"""CREATE TABLE statements for marketplace, payments, and infrastructure."""

MARKETPLACE_LISTINGS = """
CREATE TABLE marketplace_listings (
    listing_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    art_id            UUID NOT NULL REFERENCES art_pieces(art_id),
    seller_user_id    UUID NOT NULL REFERENCES users(user_id),
    asking_price_cents INTEGER NOT NULL
                      CONSTRAINT ck_listing_price_positive CHECK (asking_price_cents > 0),
    currency_code     VARCHAR(3) NOT NULL DEFAULT 'USD',
    status            VARCHAR(20) NOT NULL
                      CONSTRAINT ck_listing_status
                      CHECK (status IN ('active','sold','cancelled')),
    listed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    sold_at           TIMESTAMPTZ,
    cancelled_at      TIMESTAMPTZ
);
"""

TRANSACTIONS = """
CREATE TABLE transactions (
    transaction_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    buyer_user_id           UUID NOT NULL REFERENCES users(user_id),
    seller_user_id          UUID NOT NULL REFERENCES users(user_id),
    art_id                  UUID NOT NULL REFERENCES art_pieces(art_id),
    listing_id              UUID NOT NULL REFERENCES marketplace_listings(listing_id),
    amount_cents            INTEGER NOT NULL
                            CONSTRAINT ck_txn_amount_positive CHECK (amount_cents > 0),
    platform_fee_cents      INTEGER NOT NULL DEFAULT 0,
    seller_payout_cents     INTEGER NOT NULL,
    stripe_payment_intent_id VARCHAR(255) UNIQUE,
    status                  VARCHAR(20) NOT NULL
                            CONSTRAINT ck_txn_status
                            CHECK (status IN ('pending','completed','failed','refunded')),
    failure_reason          TEXT,
    initiated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at            TIMESTAMPTZ,
    CONSTRAINT ck_txn_fee_split
        CHECK (amount_cents = platform_fee_cents + seller_payout_cents),
    CONSTRAINT ck_txn_no_self_trade
        CHECK (buyer_user_id != seller_user_id)
);
"""

PAYMENT_RECORDS = """
CREATE TABLE payment_records (
    payment_id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                  UUID NOT NULL REFERENCES users(user_id),
    payment_type             VARCHAR(30) NOT NULL
                             CONSTRAINT ck_payment_type
                             CHECK (payment_type IN (
                                 'credit_purchase','marketplace_purchase','payout'
                             )),
    amount_cents             INTEGER NOT NULL
                             CONSTRAINT ck_payment_amount_nonneg CHECK (amount_cents >= 0),
    stripe_payment_intent_id VARCHAR(255) UNIQUE,
    status                   VARCHAR(20) NOT NULL
                             CONSTRAINT ck_payment_status
                             CHECK (status IN ('pending','completed','failed','refunded')),
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

PROCESSED_WEBHOOKS = """
CREATE TABLE processed_webhooks (
    event_id     VARCHAR(255) PRIMARY KEY,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

HMAC_KEYS = """
CREATE TABLE hmac_keys (
    version      INTEGER PRIMARY KEY,
    key_material BYTEA NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    retired_at   TIMESTAMPTZ
);
"""

TOOL_PROMPT_TEMPLATES = """
CREATE TABLE tool_prompt_templates (
    template_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tier             VARCHAR(30) NOT NULL,
    system_prompt    TEXT NOT NULL,
    tool_definitions JSONB,
    version          INTEGER NOT NULL,
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

ALL = [
    MARKETPLACE_LISTINGS,
    TRANSACTIONS,
    PAYMENT_RECORDS,
    PROCESSED_WEBHOOKS,
    HMAC_KEYS,
    TOOL_PROMPT_TEMPLATES,
]
