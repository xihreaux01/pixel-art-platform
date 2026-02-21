"""CREATE TABLE statements for users, credits, and tier definitions."""

USERS = """
CREATE TABLE users (
    user_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           VARCHAR(320) NOT NULL UNIQUE,
    username        VARCHAR(40)  NOT NULL UNIQUE,
    password_hash   VARCHAR(255) NOT NULL,
    phone_hash      VARCHAR(64)  UNIQUE,
    phone_verified  BOOLEAN NOT NULL DEFAULT FALSE,
    stripe_customer_id VARCHAR(255) UNIQUE,
    stripe_connect_id  VARCHAR(255) UNIQUE,
    token_version   INTEGER NOT NULL DEFAULT 0,
    mfa_secret_enc  BYTEA,
    mfa_enabled     BOOLEAN NOT NULL DEFAULT FALSE,
    credit_balance  INTEGER NOT NULL DEFAULT 0,
    free_generations_today INTEGER NOT NULL DEFAULT 0,
    last_free_gen_date DATE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    content_violations_count INTEGER NOT NULL DEFAULT 0
);
"""

CREDIT_PACK_DEFINITIONS = """
CREATE TABLE credit_pack_definitions (
    pack_id       SERIAL PRIMARY KEY,
    name          VARCHAR(100) NOT NULL,
    price_cents   INTEGER NOT NULL,
    credit_amount INTEGER NOT NULL,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE
);
"""

CREDIT_TRANSACTIONS = """
CREATE TABLE credit_transactions (
    txn_id      BIGSERIAL,
    user_id     UUID NOT NULL REFERENCES users(user_id),
    amount      INTEGER NOT NULL,
    txn_type    VARCHAR(30) NOT NULL
                CONSTRAINT ck_credit_txn_type
                CHECK (txn_type IN (
                    'purchase','spend','refund',
                    'compensation','admin_adjustment'
                )),
    reference_id UUID,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (txn_id, created_at)
) PARTITION BY RANGE (created_at);
"""

CREDIT_TRANSACTIONS_PARTITION = """
CREATE TABLE credit_transactions_2026_02
    PARTITION OF credit_transactions
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
"""

CREDIT_BALANCE_SNAPSHOTS = """
CREATE TABLE credit_balance_snapshots (
    snapshot_id   BIGSERIAL PRIMARY KEY,
    user_id       UUID NOT NULL REFERENCES users(user_id),
    balance       INTEGER NOT NULL,
    as_of_txn_id  BIGINT NOT NULL,
    snapshot_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_snapshot_user_txn UNIQUE (user_id, as_of_txn_id)
);
"""

GENERATION_TIER_DEFINITIONS = """
CREATE TABLE generation_tier_definitions (
    tier_id             SERIAL PRIMARY KEY,
    tier_name           VARCHAR(30) NOT NULL UNIQUE,
    canvas_width        INTEGER NOT NULL,
    canvas_height       INTEGER NOT NULL,
    credit_cost         INTEGER NOT NULL,
    tool_budget_soft    INTEGER NOT NULL,
    tool_budget_hard    INTEGER NOT NULL,
    job_timeout_seconds INTEGER NOT NULL,
    allowed_tools       JSONB
);
"""

ALL = [
    USERS,
    CREDIT_PACK_DEFINITIONS,
    CREDIT_TRANSACTIONS,
    CREDIT_TRANSACTIONS_PARTITION,
    CREDIT_BALANCE_SNAPSHOTS,
    GENERATION_TIER_DEFINITIONS,
]
