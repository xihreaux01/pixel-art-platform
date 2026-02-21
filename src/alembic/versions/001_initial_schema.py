"""Initial schema -- all tables, indexes, seed data, and protective triggers.

Revision ID: 001_initial_schema
Revises:
Create Date: 2026-02-16
"""

from alembic import op

from app.schema_sql import (
    indexes,
    seeds,
    tables_art,
    tables_core,
    tables_marketplace,
    triggers,
)

revision = "001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def _execute_all(statements: list[str]) -> None:
    """Execute a list of SQL statements sequentially."""
    for stmt in statements:
        op.execute(stmt)


def upgrade() -> None:
    _execute_all(tables_core.ALL)
    _execute_all(tables_art.ALL)
    _execute_all(tables_marketplace.ALL)
    _execute_all(indexes.ALL)
    _execute_all(seeds.ALL)
    _execute_all(triggers.FUNCTIONS_ALL)
    _execute_all(triggers.TRIGGERS_ALL)


def downgrade() -> None:
    _drop_triggers()
    _drop_functions()
    _drop_tables()


def _drop_triggers() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_listing_flag_sync ON marketplace_listings;")
    op.execute("DROP TRIGGER IF EXISTS trg_ownership_update_owner ON ownership_history;")
    op.execute("DROP TRIGGER IF EXISTS trg_art_immutable_fields ON art_pieces;")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_credit_transactions_immutable "
        "ON credit_transactions;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_processed_webhooks_immutable "
        "ON processed_webhooks;"
    )


def _drop_functions() -> None:
    op.execute("DROP FUNCTION IF EXISTS update_listing_flag();")
    op.execute("DROP FUNCTION IF EXISTS update_current_owner();")
    op.execute("DROP FUNCTION IF EXISTS check_immutable_art_fields();")
    op.execute("DROP FUNCTION IF EXISTS raise_immutable_error();")


def _drop_tables() -> None:
    tables = [
        "tool_prompt_templates",
        "hmac_keys",
        "processed_webhooks",
        "payment_records",
        "transactions",
        "marketplace_listings",
        "tool_call_archives",
        "generation_summaries",
        "art_generation_jobs",
        "ownership_history",
        "art_pieces",
        "credit_balance_snapshots",
        "credit_transactions_2026_02",
        "credit_transactions",
        "generation_tier_definitions",
        "credit_pack_definitions",
        "users",
    ]
    for table in tables:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE;")
