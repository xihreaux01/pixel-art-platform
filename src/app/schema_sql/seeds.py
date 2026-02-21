"""Seed data INSERT statements."""

CREDIT_PACKS = """
INSERT INTO credit_pack_definitions (name, price_cents, credit_amount, is_active)
VALUES
    ('Starter', 499, 50, TRUE),
    ('Value',   999, 120, TRUE),
    ('Pro',     1999, 300, TRUE);
"""

GENERATION_TIERS = """
INSERT INTO generation_tier_definitions
    (tier_name, canvas_width, canvas_height, credit_cost,
     tool_budget_soft, tool_budget_hard, job_timeout_seconds, allowed_tools)
VALUES
    ('small',  32, 32, 1, 50, 75, 120,
     '["draw_pixel","draw_line","fill_rect","fill_bucket","set_palette"]'::jsonb),
    ('medium', 64, 64, 3, 150, 200, 300,
     '["draw_pixel","draw_line","fill_rect","fill_bucket","set_palette","draw_circle","draw_ellipse"]'::jsonb),
    ('large',  128, 128, 8, 400, 500, 600,
     '["draw_pixel","draw_line","fill_rect","fill_bucket","set_palette","draw_circle","draw_ellipse","draw_bezier","gradient_fill"]'::jsonb);
"""

ALL = [CREDIT_PACKS, GENERATION_TIERS]
