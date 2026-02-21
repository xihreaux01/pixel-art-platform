"""Tool definitions for the pixel art generation system.

Defines the ToolName enum and per-tool Pydantic argument models with strict
validation (extra="forbid") so that any unexpected fields from the LLM
are immediately rejected.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, model_validator


class ToolName(str, Enum):
    """Canonical names for every drawing tool the LLM may invoke."""

    SET_PIXEL = "set_pixel"
    FILL_RECT = "fill_rect"
    SET_PALETTE = "set_palette"
    SEAL_CANVAS = "seal_canvas"
    DRAW_LINE = "draw_line"
    DRAW_CIRCLE = "draw_circle"
    FLOOD_FILL = "flood_fill"
    GRADIENT_FILL = "gradient_fill"
    DITHER = "dither"
    MIRROR = "mirror"
    ROTATE = "rotate"


# ---------------------------------------------------------------------------
# Per-tool argument models
# ---------------------------------------------------------------------------

class SetPixelArgs(BaseModel, extra="forbid"):
    """Arguments for the set_pixel tool."""

    x: int = Field(ge=0, description="X coordinate")
    y: int = Field(ge=0, description="Y coordinate")
    r: int = Field(ge=0, le=255)
    g: int = Field(ge=0, le=255)
    b: int = Field(ge=0, le=255)


class FillRectArgs(BaseModel, extra="forbid"):
    """Arguments for the fill_rect tool."""

    x1: int = Field(ge=0)
    y1: int = Field(ge=0)
    x2: int = Field(ge=0)
    y2: int = Field(ge=0)
    r: int = Field(ge=0, le=255)
    g: int = Field(ge=0, le=255)
    b: int = Field(ge=0, le=255)

    @model_validator(mode="after")
    def _check_rect_order(self) -> FillRectArgs:
        if self.x2 < self.x1 or self.y2 < self.y1:
            raise ValueError(
                "x2 must be >= x1 and y2 must be >= y1 for fill_rect"
            )
        return self


class SetPaletteArgs(BaseModel, extra="forbid"):
    """Arguments for the set_palette tool."""

    colors: list[list[int]] = Field(
        description="List of [r, g, b] colors",
        max_length=16,
    )

    @model_validator(mode="after")
    def _validate_colors(self) -> SetPaletteArgs:
        for i, color in enumerate(self.colors):
            if len(color) != 3:
                raise ValueError(
                    f"Color at index {i} must have exactly 3 values [r, g, b]"
                )
            for c in color:
                if not (0 <= c <= 255):
                    raise ValueError(
                        f"Color component at index {i} out of range 0-255"
                    )
        return self


class SealCanvasArgs(BaseModel, extra="forbid"):
    """Arguments for the seal_canvas tool (no args needed)."""

    pass


class DrawLineArgs(BaseModel, extra="forbid"):
    """Arguments for the draw_line tool."""

    x1: int = Field(ge=0)
    y1: int = Field(ge=0)
    x2: int = Field(ge=0)
    y2: int = Field(ge=0)
    r: int = Field(ge=0, le=255)
    g: int = Field(ge=0, le=255)
    b: int = Field(ge=0, le=255)


class DrawCircleArgs(BaseModel, extra="forbid"):
    """Arguments for the draw_circle tool."""

    cx: int = Field(ge=0, description="Center X")
    cy: int = Field(ge=0, description="Center Y")
    radius: int = Field(ge=1, le=32)
    r: int = Field(ge=0, le=255)
    g: int = Field(ge=0, le=255)
    b: int = Field(ge=0, le=255)
    fill: bool = Field(default=False, description="Fill the circle")


class FloodFillArgs(BaseModel, extra="forbid"):
    """Arguments for the flood_fill tool."""

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    r: int = Field(ge=0, le=255)
    g: int = Field(ge=0, le=255)
    b: int = Field(ge=0, le=255)


class GradientFillArgs(BaseModel, extra="forbid"):
    """Arguments for the gradient_fill tool."""

    x1: int = Field(ge=0)
    y1: int = Field(ge=0)
    x2: int = Field(ge=0)
    y2: int = Field(ge=0)
    r1: int = Field(ge=0, le=255)
    g1: int = Field(ge=0, le=255)
    b1: int = Field(ge=0, le=255)
    r2: int = Field(ge=0, le=255)
    g2: int = Field(ge=0, le=255)
    b2: int = Field(ge=0, le=255)
    direction: str = Field(
        default="horizontal", pattern=r"^(horizontal|vertical)$"
    )


class DitherArgs(BaseModel, extra="forbid"):
    """Arguments for the dither tool."""

    x1: int = Field(ge=0)
    y1: int = Field(ge=0)
    x2: int = Field(ge=0)
    y2: int = Field(ge=0)
    r1: int = Field(ge=0, le=255)
    g1: int = Field(ge=0, le=255)
    b1: int = Field(ge=0, le=255)
    r2: int = Field(ge=0, le=255)
    g2: int = Field(ge=0, le=255)
    b2: int = Field(ge=0, le=255)


class MirrorArgs(BaseModel, extra="forbid"):
    """Arguments for the mirror tool."""

    axis: str = Field(pattern=r"^(horizontal|vertical)$")


class RotateArgs(BaseModel, extra="forbid"):
    """Arguments for the rotate tool."""

    degrees: int = Field(description="Rotation in degrees", ge=0, lt=360)
