"""All CREATE INDEX statements for the initial schema."""

ALL = [
    # users
    "CREATE INDEX idx_users_phone ON users(phone_hash) WHERE phone_hash IS NOT NULL;",
    # art_pieces
    "CREATE INDEX idx_art_owner ON art_pieces(current_owner_id);",
    "CREATE INDEX idx_art_creator ON art_pieces(creator_user_id);",
    "CREATE INDEX idx_art_listed ON art_pieces(is_marketplace_listed) "
    "WHERE is_marketplace_listed = TRUE;",
    # ownership_history
    "CREATE INDEX idx_ownership_art ON ownership_history(art_id, transferred_at DESC);",
    # marketplace_listings
    "CREATE INDEX idx_listings_active ON marketplace_listings(status, listed_at DESC) "
    "WHERE status = 'active';",
    "CREATE INDEX idx_listings_seller ON marketplace_listings(seller_user_id, status);",
    "CREATE UNIQUE INDEX idx_one_active_listing_per_art "
    "ON marketplace_listings(art_id) WHERE status = 'active';",
    # transactions
    "CREATE INDEX idx_txn_buyer ON transactions(buyer_user_id, completed_at DESC);",
    "CREATE INDEX idx_txn_seller ON transactions(seller_user_id, completed_at DESC);",
    # art_generation_jobs
    "CREATE INDEX idx_jobs_user ON art_generation_jobs(user_id, created_at DESC);",
    "CREATE INDEX idx_jobs_pending ON art_generation_jobs(status) "
    "WHERE status IN ('pending', 'executing_tools');",
    # credit_transactions
    "CREATE INDEX idx_credit_txn_user ON credit_transactions(user_id, txn_id);",
    # credit_balance_snapshots
    "CREATE INDEX idx_snapshot_user_latest "
    "ON credit_balance_snapshots(user_id, as_of_txn_id DESC);",
    # tool_call_archives
    "CREATE INDEX idx_archive_cleanup ON tool_call_archives(created_at, job_id);",
]
