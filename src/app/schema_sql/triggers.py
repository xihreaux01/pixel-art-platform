"""Trigger functions and trigger DDL for the initial schema."""

# ---- Trigger functions ----

FN_RAISE_IMMUTABLE = """
CREATE OR REPLACE FUNCTION raise_immutable_error()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Rows in table % are immutable', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;
"""

FN_CHECK_IMMUTABLE_ART = """
CREATE OR REPLACE FUNCTION check_immutable_art_fields()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.generation_hash   IS DISTINCT FROM NEW.generation_hash
    OR OLD.seal_signature    IS DISTINCT FROM NEW.seal_signature
    OR OLD.creator_user_id   IS DISTINCT FROM NEW.creator_user_id
    OR OLD.canvas_width      IS DISTINCT FROM NEW.canvas_width
    OR OLD.canvas_height     IS DISTINCT FROM NEW.canvas_height
    OR OLD.rendered_image_path IS DISTINCT FROM NEW.rendered_image_path
    THEN
        RAISE EXCEPTION 'Cannot modify sealed art fields';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

FN_UPDATE_CURRENT_OWNER = """
CREATE OR REPLACE FUNCTION update_current_owner()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE art_pieces
       SET current_owner_id = NEW.to_user_id,
           times_traded = times_traded + 1
     WHERE art_id = NEW.art_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

FN_UPDATE_LISTING_FLAG = """
CREATE OR REPLACE FUNCTION update_listing_flag()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' AND NEW.status = 'active' THEN
        UPDATE art_pieces
           SET is_marketplace_listed = TRUE
         WHERE art_id = NEW.art_id;
    ELSIF TG_OP = 'UPDATE'
          AND OLD.status = 'active'
          AND NEW.status != 'active' THEN
        UPDATE art_pieces
           SET is_marketplace_listed = FALSE
         WHERE art_id = NEW.art_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

FUNCTIONS_ALL = [
    FN_RAISE_IMMUTABLE,
    FN_CHECK_IMMUTABLE_ART,
    FN_UPDATE_CURRENT_OWNER,
    FN_UPDATE_LISTING_FLAG,
]

# ---- Triggers ----

TRIGGERS_ALL = [
    "CREATE TRIGGER trg_processed_webhooks_immutable "
    "BEFORE UPDATE OR DELETE ON processed_webhooks "
    "FOR EACH ROW EXECUTE FUNCTION raise_immutable_error();",

    "CREATE TRIGGER trg_credit_transactions_immutable "
    "BEFORE UPDATE OR DELETE ON credit_transactions "
    "FOR EACH ROW EXECUTE FUNCTION raise_immutable_error();",

    "CREATE TRIGGER trg_art_immutable_fields "
    "BEFORE UPDATE ON art_pieces "
    "FOR EACH ROW EXECUTE FUNCTION check_immutable_art_fields();",

    "CREATE TRIGGER trg_ownership_update_owner "
    "AFTER INSERT ON ownership_history "
    "FOR EACH ROW EXECUTE FUNCTION update_current_owner();",

    "CREATE TRIGGER trg_listing_flag_sync "
    "AFTER INSERT OR UPDATE ON marketplace_listings "
    "FOR EACH ROW EXECUTE FUNCTION update_listing_flag();",
]
