"""Ollama tool-calling schemas and descriptions for pixel art tools.

Provides the JSON schemas that Ollama expects in the ``tools`` array of
a ``/api/chat`` request, plus human-readable descriptions used in the
system prompt.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Tool descriptions (used to build the system prompt)
# ---------------------------------------------------------------------------

TOOL_DESCRIPTIONS: dict[str, str] = {
    "set_pixel": "Set a single pixel at (x, y) to RGB color (r, g, b).",
    "fill_rect": "Fill a rectangle from (x1, y1) to (x2, y2) with RGB color (r, g, b).",
    "set_palette": "Set a color palette of up to 16 [r, g, b] colors.",
    "seal_canvas": "Finalize the canvas. Call this when the artwork is complete.",
    "draw_line": "Draw a line from (x1, y1) to (x2, y2) with RGB color (r, g, b).",
    "draw_circle": "Draw a circle at center (cx, cy) with given radius and RGB color. Set fill=true to fill.",
    "flood_fill": "Flood-fill a contiguous region starting at (x, y) with RGB color (r, g, b).",
    "gradient_fill": "Fill a rectangle with a gradient from color1 to color2. Direction: horizontal|vertical.",
    "dither": "Apply a checkerboard dither pattern in a rectangle using two RGB colors.",
    "mirror": "Mirror the entire canvas. Axis: horizontal or vertical.",
    "rotate": "Rotate the entire canvas by the given number of degrees (0-359).",
}

# ---------------------------------------------------------------------------
# Tool schemas (Ollama function-calling format)
# ---------------------------------------------------------------------------

_rgb_props = {
    "r": {"type": "integer"}, "g": {"type": "integer"}, "b": {"type": "integer"},
}
_rgb_required = ["r", "g", "b"]

_rect_props = {
    "x1": {"type": "integer"}, "y1": {"type": "integer"},
    "x2": {"type": "integer"}, "y2": {"type": "integer"},
}
_rect_required = ["x1", "y1", "x2", "y2"]


def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
    """Build a single Ollama tool schema entry."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


TOOL_SCHEMAS: dict[str, dict] = {
    "set_pixel": _fn(
        "set_pixel", "Set a single pixel at (x, y) to an RGB color.",
        {"x": {"type": "integer"}, "y": {"type": "integer"}, **_rgb_props},
        ["x", "y", *_rgb_required],
    ),
    "fill_rect": _fn(
        "fill_rect", "Fill a rectangle from (x1,y1) to (x2,y2) with RGB color.",
        {**_rect_props, **_rgb_props},
        [*_rect_required, *_rgb_required],
    ),
    "set_palette": _fn(
        "set_palette", "Set a color palette (up to 16 colors).",
        {"colors": {"type": "array", "items": {"type": "array", "items": {"type": "integer"}}}},
        ["colors"],
    ),
    "seal_canvas": _fn(
        "seal_canvas", "Finalize the canvas. Call when artwork is complete.",
        {}, [],
    ),
    "draw_line": _fn(
        "draw_line", "Draw a line from (x1,y1) to (x2,y2) with RGB color.",
        {**_rect_props, **_rgb_props},
        [*_rect_required, *_rgb_required],
    ),
    "draw_circle": _fn(
        "draw_circle", "Draw a circle at center (cx,cy) with radius and RGB color.",
        {
            "cx": {"type": "integer"}, "cy": {"type": "integer"},
            "radius": {"type": "integer"}, **_rgb_props,
            "fill": {"type": "boolean", "default": False},
        },
        ["cx", "cy", "radius", *_rgb_required],
    ),
    "flood_fill": _fn(
        "flood_fill", "Flood-fill contiguous region at (x,y) with RGB color.",
        {"x": {"type": "integer"}, "y": {"type": "integer"}, **_rgb_props},
        ["x", "y", *_rgb_required],
    ),
    "gradient_fill": _fn(
        "gradient_fill", "Fill rectangle with gradient from color1 to color2.",
        {
            **_rect_props,
            "r1": {"type": "integer"}, "g1": {"type": "integer"}, "b1": {"type": "integer"},
            "r2": {"type": "integer"}, "g2": {"type": "integer"}, "b2": {"type": "integer"},
            "direction": {"type": "string", "enum": ["horizontal", "vertical"]},
        },
        [*_rect_required, "r1", "g1", "b1", "r2", "g2", "b2"],
    ),
    "dither": _fn(
        "dither", "Apply checkerboard dither in rectangle with two colors.",
        {
            **_rect_props,
            "r1": {"type": "integer"}, "g1": {"type": "integer"}, "b1": {"type": "integer"},
            "r2": {"type": "integer"}, "g2": {"type": "integer"}, "b2": {"type": "integer"},
        },
        [*_rect_required, "r1", "g1", "b1", "r2", "g2", "b2"],
    ),
    "mirror": _fn(
        "mirror", "Mirror the entire canvas along an axis.",
        {"axis": {"type": "string", "enum": ["horizontal", "vertical"]}},
        ["axis"],
    ),
    "rotate": _fn(
        "rotate", "Rotate the entire canvas by degrees (0-359).",
        {"degrees": {"type": "integer"}},
        ["degrees"],
    ),
}


def build_tools_schema(allowed_tools: list[str]) -> list[dict]:
    """Return the Ollama tools array filtered to *allowed_tools*."""
    return [TOOL_SCHEMAS[name] for name in allowed_tools if name in TOOL_SCHEMAS]
