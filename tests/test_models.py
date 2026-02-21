"""Tests for ORM model imports, table names, and relationships."""

import pytest

from app.models import (
    ArtGenerationJob,
    ArtPiece,
    Base,
    CreditBalanceSnapshot,
    CreditPackDefinition,
    CreditTransaction,
    GenerationSummary,
    GenerationTierDefinition,
    HmacKey,
    MarketplaceListing,
    OwnershipHistory,
    PaymentRecord,
    ProcessedWebhook,
    ToolCallArchive,
    ToolPromptTemplate,
    Transaction,
    User,
)

# All model classes paired with their expected table names
MODEL_TABLE_PAIRS = [
    (User, "users"),
    (CreditPackDefinition, "credit_pack_definitions"),
    (CreditTransaction, "credit_transactions"),
    (CreditBalanceSnapshot, "credit_balance_snapshots"),
    (GenerationTierDefinition, "generation_tier_definitions"),
    (ArtPiece, "art_pieces"),
    (OwnershipHistory, "ownership_history"),
    (ArtGenerationJob, "art_generation_jobs"),
    (GenerationSummary, "generation_summaries"),
    (ToolCallArchive, "tool_call_archives"),
    (MarketplaceListing, "marketplace_listings"),
    (Transaction, "transactions"),
    (PaymentRecord, "payment_records"),
    (ProcessedWebhook, "processed_webhooks"),
    (HmacKey, "hmac_keys"),
    (ToolPromptTemplate, "tool_prompt_templates"),
]


class TestModelImports:
    """Verify all 16 model classes are importable."""

    @pytest.mark.parametrize(
        "model_cls,expected_table",
        MODEL_TABLE_PAIRS,
        ids=[pair[1] for pair in MODEL_TABLE_PAIRS],
    )
    def test_model_importable_and_table_name(self, model_cls, expected_table):
        assert model_cls.__tablename__ == expected_table


class TestBaseMetadata:
    """Verify the Base metadata registers all 16 tables."""

    def test_all_tables_registered(self):
        registered = set(Base.metadata.tables.keys())
        expected = {pair[1] for pair in MODEL_TABLE_PAIRS}
        assert expected.issubset(registered)

    def test_table_count(self):
        assert len(Base.metadata.tables) == 16


class TestUserRelationships:
    """Verify User has the expected relationship attributes."""

    @pytest.mark.parametrize(
        "attr",
        [
            "credit_transactions",
            "credit_balance_snapshots",
            "created_art_pieces",
            "owned_art_pieces",
            "generation_jobs",
            "marketplace_listings",
            "buyer_transactions",
            "seller_transactions",
            "payment_records",
        ],
    )
    def test_user_has_relationship(self, attr):
        mapper = User.__mapper__
        assert attr in mapper.relationships


class TestArtPieceRelationships:
    """Verify ArtPiece has the expected relationship attributes."""

    @pytest.mark.parametrize(
        "attr",
        [
            "creator",
            "current_owner",
            "ownership_history",
            "generation_job",
            "generation_summaries",
            "marketplace_listings",
            "sale_transactions",
        ],
    )
    def test_art_piece_has_relationship(self, attr):
        mapper = ArtPiece.__mapper__
        assert attr in mapper.relationships


class TestTransactionRelationships:
    """Verify Transaction has buyer, seller, art_piece, listing."""

    @pytest.mark.parametrize(
        "attr", ["buyer", "seller", "art_piece", "listing"]
    )
    def test_transaction_has_relationship(self, attr):
        mapper = Transaction.__mapper__
        assert attr in mapper.relationships


class TestMigrationSyntax:
    """Verify the migration file and SQL modules are syntactically valid."""

    def test_migration_compiles(self):
        import py_compile

        py_compile.compile(
            "src/alembic/versions/001_initial_schema.py", doraise=True
        )

    def test_sql_modules_import(self):
        from app.schema_sql import (
            indexes,
            seeds,
            tables_art,
            tables_core,
            tables_marketplace,
            triggers,
        )

        assert len(tables_core.ALL) > 0
        assert len(tables_art.ALL) > 0
        assert len(tables_marketplace.ALL) > 0
        assert len(indexes.ALL) > 0
        assert len(seeds.ALL) > 0
        assert len(triggers.FUNCTIONS_ALL) > 0
        assert len(triggers.TRIGGERS_ALL) > 0
