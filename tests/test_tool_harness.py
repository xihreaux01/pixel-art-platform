"""Comprehensive tests for the tool harness and executor layer.

Run with:
    ./venv/bin/python -m pytest tests/test_tool_harness.py -v
"""

from __future__ import annotations

import pytest
from PIL import Image

from app.services.tool_harness import HarnessConfig, ToolCallResult, ToolHarness
from app.services.tools.definitions import ToolName


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_config(**overrides) -> HarnessConfig:
    """Return a small-canvas config with all tools allowed."""
    defaults = dict(
        canvas_width=16,
        canvas_height=16,
        allowed_tools=[t.value for t in ToolName],
        tool_budget_hard=100,
    )
    defaults.update(overrides)
    return HarnessConfig(**defaults)


def _make_harness(**overrides) -> ToolHarness:
    return ToolHarness(_default_config(**overrides))


# ---------------------------------------------------------------------------
# 1. test_valid_set_pixel
# ---------------------------------------------------------------------------

class TestSetPixel:
    def test_valid_set_pixel(self):
        harness = _make_harness()
        result = harness.execute("set_pixel", {"x": 5, "y": 7, "r": 255, "g": 128, "b": 0})

        assert result.success is True
        assert result.tool_name == "set_pixel"
        assert harness.canvas.getpixel((5, 7)) == (255, 128, 0)
        assert harness.tool_calls_executed == 1


# ---------------------------------------------------------------------------
# 2. test_valid_fill_rect
# ---------------------------------------------------------------------------

class TestFillRect:
    def test_valid_fill_rect(self):
        harness = _make_harness()
        result = harness.execute(
            "fill_rect",
            {"x1": 0, "y1": 0, "x2": 3, "y2": 3, "r": 100, "g": 200, "b": 50},
        )

        assert result.success is True
        # Every pixel in the rectangle should be the fill color.
        for y in range(4):
            for x in range(4):
                assert harness.canvas.getpixel((x, y)) == (100, 200, 50)
        # A pixel outside the rectangle should still be black.
        assert harness.canvas.getpixel((4, 4)) == (0, 0, 0)


# ---------------------------------------------------------------------------
# 3. test_invalid_tool_name
# ---------------------------------------------------------------------------

class TestInvalidToolName:
    def test_invalid_tool_name(self):
        harness = _make_harness()
        result = harness.execute("explode_canvas", {"x": 0})

        assert result.success is False
        assert "Unknown tool" in result.message
        assert harness.tool_calls_executed == 0


# ---------------------------------------------------------------------------
# 4. test_wrong_tier_tool
# ---------------------------------------------------------------------------

class TestWrongTierTool:
    def test_wrong_tier_tool(self):
        """A tool that exists but is not in the tier's allowed list."""
        harness = _make_harness(allowed_tools=["set_pixel", "fill_rect"])
        result = harness.execute(
            "draw_line",
            {"x1": 0, "y1": 0, "x2": 5, "y2": 5, "r": 255, "g": 0, "b": 0},
        )

        assert result.success is False
        assert "not allowed" in result.message
        assert harness.tool_calls_executed == 0


# ---------------------------------------------------------------------------
# 5. test_out_of_bounds_coords
# ---------------------------------------------------------------------------

class TestOutOfBoundsCoords:
    def test_out_of_bounds_x(self):
        harness = _make_harness(canvas_width=16, canvas_height=16)
        result = harness.execute(
            "set_pixel", {"x": 16, "y": 0, "r": 0, "g": 0, "b": 0}
        )
        assert result.success is False
        assert "out of bounds" in result.message

    def test_out_of_bounds_y(self):
        harness = _make_harness(canvas_width=16, canvas_height=16)
        result = harness.execute(
            "set_pixel", {"x": 0, "y": 16, "r": 0, "g": 0, "b": 0}
        )
        assert result.success is False
        assert "out of bounds" in result.message

    def test_fill_rect_out_of_bounds(self):
        harness = _make_harness(canvas_width=8, canvas_height=8)
        result = harness.execute(
            "fill_rect",
            {"x1": 0, "y1": 0, "x2": 8, "y2": 3, "r": 0, "g": 0, "b": 0},
        )
        assert result.success is False
        assert "out of bounds" in result.message


# ---------------------------------------------------------------------------
# 6. test_extra_fields_rejected
# ---------------------------------------------------------------------------

class TestExtraFieldsRejected:
    def test_extra_fields_rejected(self):
        harness = _make_harness()
        result = harness.execute(
            "set_pixel",
            {"x": 0, "y": 0, "r": 0, "g": 0, "b": 0, "sneaky": True},
        )
        assert result.success is False
        assert "validation failed" in result.message.lower() or "extra" in result.message.lower()


# ---------------------------------------------------------------------------
# 7. test_negative_color_rejected
# ---------------------------------------------------------------------------

class TestNegativeColorRejected:
    def test_negative_color_rejected(self):
        harness = _make_harness()
        result = harness.execute(
            "set_pixel", {"x": 0, "y": 0, "r": -1, "g": 0, "b": 0}
        )
        assert result.success is False
        assert "validation failed" in result.message.lower() or "greater than" in result.message.lower()


# ---------------------------------------------------------------------------
# 8. test_color_overflow_rejected
# ---------------------------------------------------------------------------

class TestColorOverflowRejected:
    def test_color_overflow_rejected(self):
        harness = _make_harness()
        result = harness.execute(
            "set_pixel", {"x": 0, "y": 0, "r": 256, "g": 0, "b": 0}
        )
        assert result.success is False
        assert "validation failed" in result.message.lower() or "less than" in result.message.lower()


# ---------------------------------------------------------------------------
# 9. test_budget_exceeded
# ---------------------------------------------------------------------------

class TestBudgetExceeded:
    def test_budget_exceeded(self):
        harness = _make_harness(tool_budget_hard=3)

        # Execute 3 valid calls -- should all succeed.
        for i in range(3):
            result = harness.execute(
                "set_pixel", {"x": i, "y": 0, "r": 255, "g": 0, "b": 0}
            )
            assert result.success is True

        # The 4th call should fail.
        result = harness.execute(
            "set_pixel", {"x": 0, "y": 1, "r": 255, "g": 0, "b": 0}
        )
        assert result.success is False
        assert "budget" in result.message.lower()
        assert harness.tool_calls_executed == 3


# ---------------------------------------------------------------------------
# 10. test_seal_canvas
# ---------------------------------------------------------------------------

class TestSealCanvas:
    def test_seal_canvas(self):
        harness = _make_harness()

        # Seal the canvas.
        result = harness.execute("seal_canvas", {})
        assert result.success is True
        assert harness.sealed is True
        assert "sealed" in result.message.lower()

        # Any subsequent call should be rejected.
        result = harness.execute(
            "set_pixel", {"x": 0, "y": 0, "r": 255, "g": 0, "b": 0}
        )
        assert result.success is False
        assert "sealed" in result.message.lower()


# ---------------------------------------------------------------------------
# 11. test_draw_line
# ---------------------------------------------------------------------------

class TestDrawLine:
    def test_draw_line(self):
        harness = _make_harness()
        result = harness.execute(
            "draw_line",
            {"x1": 0, "y1": 0, "x2": 15, "y2": 0, "r": 255, "g": 0, "b": 0},
        )
        assert result.success is True

        # The horizontal line should have coloured every pixel in row 0.
        for x in range(16):
            assert harness.canvas.getpixel((x, 0)) == (255, 0, 0)


# ---------------------------------------------------------------------------
# 12. test_draw_circle
# ---------------------------------------------------------------------------

class TestDrawCircle:
    def test_draw_circle_filled(self):
        harness = _make_harness(canvas_width=32, canvas_height=32)
        result = harness.execute(
            "draw_circle",
            {"cx": 15, "cy": 15, "radius": 5, "r": 0, "g": 255, "b": 0, "fill": True},
        )
        assert result.success is True

        # The center should be the fill color.
        assert harness.canvas.getpixel((15, 15)) == (0, 255, 0)

    def test_draw_circle_outline(self):
        harness = _make_harness(canvas_width=32, canvas_height=32)
        result = harness.execute(
            "draw_circle",
            {"cx": 15, "cy": 15, "radius": 5, "r": 0, "g": 255, "b": 0, "fill": False},
        )
        assert result.success is True

        # The center of an outline-only circle should remain black.
        assert harness.canvas.getpixel((15, 15)) == (0, 0, 0)


# ---------------------------------------------------------------------------
# Additional executor-level tests
# ---------------------------------------------------------------------------

class TestFloodFill:
    def test_flood_fill(self):
        harness = _make_harness()
        # Fill a small rect first, then flood-fill it.
        harness.execute(
            "fill_rect",
            {"x1": 0, "y1": 0, "x2": 3, "y2": 3, "r": 100, "g": 100, "b": 100},
        )
        result = harness.execute(
            "flood_fill",
            {"x": 1, "y": 1, "r": 200, "g": 50, "b": 50},
        )
        assert result.success is True
        # The seed point should now be the flood-fill color.
        assert harness.canvas.getpixel((1, 1)) == (200, 50, 50)


class TestGradientFill:
    def test_horizontal_gradient(self):
        harness = _make_harness()
        result = harness.execute(
            "gradient_fill",
            {
                "x1": 0, "y1": 0, "x2": 15, "y2": 0,
                "r1": 0, "g1": 0, "b1": 0,
                "r2": 255, "g2": 255, "b2": 255,
                "direction": "horizontal",
            },
        )
        assert result.success is True
        # First pixel should be ~(0,0,0), last should be (255,255,255).
        assert harness.canvas.getpixel((0, 0)) == (0, 0, 0)
        assert harness.canvas.getpixel((15, 0)) == (255, 255, 255)


class TestDither:
    def test_dither_checkerboard(self):
        harness = _make_harness()
        result = harness.execute(
            "dither",
            {
                "x1": 0, "y1": 0, "x2": 3, "y2": 3,
                "r1": 255, "g1": 0, "b1": 0,
                "r2": 0, "g2": 0, "b2": 255,
            },
        )
        assert result.success is True
        # (0,0) -> color1, (1,0) -> color2, (0,1) -> color2, (1,1) -> color1
        assert harness.canvas.getpixel((0, 0)) == (255, 0, 0)
        assert harness.canvas.getpixel((1, 0)) == (0, 0, 255)
        assert harness.canvas.getpixel((0, 1)) == (0, 0, 255)
        assert harness.canvas.getpixel((1, 1)) == (255, 0, 0)


class TestMirror:
    def test_mirror_horizontal(self):
        harness = _make_harness()
        # Place a red pixel at top-left corner.
        harness.execute(
            "set_pixel", {"x": 0, "y": 0, "r": 255, "g": 0, "b": 0}
        )
        result = harness.execute("mirror", {"axis": "horizontal"})
        assert result.success is True
        # After horizontal mirror, the pixel should be at the top-right.
        assert harness.canvas.getpixel((15, 0)) == (255, 0, 0)
        # Original position should now be black.
        assert harness.canvas.getpixel((0, 0)) == (0, 0, 0)


class TestRotate:
    def test_rotate(self):
        harness = _make_harness()
        result = harness.execute("rotate", {"degrees": 90})
        assert result.success is True
        assert "rotated" in result.message.lower()


class TestSetPalette:
    def test_set_palette(self):
        harness = _make_harness()
        result = harness.execute(
            "set_palette",
            {"colors": [[255, 0, 0], [0, 255, 0], [0, 0, 255]]},
        )
        assert result.success is True
        assert hasattr(harness.canvas, "_palette_hint")
        assert len(harness.canvas._palette_hint) == 3

    def test_set_palette_invalid_color(self):
        harness = _make_harness()
        result = harness.execute(
            "set_palette",
            {"colors": [[256, 0, 0]]},
        )
        assert result.success is False

    def test_set_palette_wrong_length(self):
        harness = _make_harness()
        result = harness.execute(
            "set_palette",
            {"colors": [[255, 0]]},
        )
        assert result.success is False


class TestDispatchTableCompleteness:
    """Ensure every ToolName enum member has a dispatch entry."""

    def test_all_tools_dispatched(self):
        for tool in ToolName:
            assert tool in ToolHarness.DISPATCH, (
                f"ToolName.{tool.name} has no DISPATCH entry"
            )
