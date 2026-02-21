"""CREATE TABLE statements for art pieces, jobs, summaries, and archives."""

ART_PIECES = """
CREATE TABLE art_pieces (
    art_id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    creator_user_id       UUID NOT NULL REFERENCES users(user_id),
    current_owner_id      UUID NOT NULL REFERENCES users(user_id),
    generation_tier       VARCHAR(30) NOT NULL,
    canvas_width          INTEGER NOT NULL
                          CONSTRAINT ck_art_canvas_width_positive CHECK (canvas_width > 0),
    canvas_height         INTEGER NOT NULL
                          CONSTRAINT ck_art_canvas_height_positive CHECK (canvas_height > 0),
    model_name            VARCHAR(100) NOT NULL,
    model_version         VARCHAR(50),
    rendered_image_path   VARCHAR(500) NOT NULL,
    thumbnail_path        VARCHAR(500),
    generation_hash       VARCHAR(128) NOT NULL UNIQUE,
    seal_signature        VARCHAR(512) NOT NULL,
    seal_key_version      INTEGER NOT NULL DEFAULT 1,
    is_tradeable          BOOLEAN NOT NULL DEFAULT FALSE,
    is_marketplace_listed BOOLEAN NOT NULL DEFAULT FALSE,
    times_traded          INTEGER NOT NULL DEFAULT 0,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

OWNERSHIP_HISTORY = """
CREATE TABLE ownership_history (
    record_id       BIGSERIAL PRIMARY KEY,
    art_id          UUID NOT NULL REFERENCES art_pieces(art_id),
    from_user_id    UUID REFERENCES users(user_id),
    to_user_id      UUID NOT NULL REFERENCES users(user_id),
    transfer_type   VARCHAR(20) NOT NULL
                    CONSTRAINT ck_ownership_transfer_type
                    CHECK (transfer_type IN ('creation','trade','gift','admin_transfer')),
    transaction_id  UUID,
    transferred_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

ART_GENERATION_JOBS = """
CREATE TABLE art_generation_jobs (
    job_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(user_id),
    generation_tier     VARCHAR(30) NOT NULL,
    idempotency_key     VARCHAR(255) UNIQUE,
    status              VARCHAR(30) NOT NULL
                        CONSTRAINT ck_job_status
                        CHECK (status IN (
                            'pending','executing_tools','rendering',
                            'completed','failed','cancelled'
                        )),
    art_id              UUID REFERENCES art_pieces(art_id),
    tool_calls_executed INTEGER NOT NULL DEFAULT 0,
    checkpoint_canvas   BYTEA,
    checkpoint_tool_idx INTEGER NOT NULL DEFAULT 0,
    last_checkpoint_at  TIMESTAMPTZ,
    last_progress_at    TIMESTAMPTZ,
    error_message       TEXT,
    compensation_type   VARCHAR(30),
    compensation_amount INTEGER,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at        TIMESTAMPTZ
);
"""

GENERATION_SUMMARIES = """
CREATE TABLE generation_summaries (
    summary_id            BIGSERIAL PRIMARY KEY,
    job_id                UUID NOT NULL REFERENCES art_generation_jobs(job_id),
    art_id                UUID REFERENCES art_pieces(art_id),
    total_tool_calls      INTEGER NOT NULL,
    tool_call_breakdown   JSONB,
    first_tool_call_at    TIMESTAMPTZ NOT NULL,
    last_tool_call_at     TIMESTAMPTZ NOT NULL,
    generation_duration_ms INTEGER NOT NULL,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

TOOL_CALL_ARCHIVES = """
CREATE TABLE tool_call_archives (
    archive_id             BIGSERIAL PRIMARY KEY,
    job_id                 UUID NOT NULL REFERENCES art_generation_jobs(job_id),
    tool_calls_gz          BYTEA NOT NULL,
    toolcall_sequence_hash VARCHAR(128),
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

ALL = [
    ART_PIECES,
    OWNERSHIP_HISTORY,
    ART_GENERATION_JOBS,
    GENERATION_SUMMARIES,
    TOOL_CALL_ARCHIVES,
]
