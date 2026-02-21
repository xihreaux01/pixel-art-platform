"""Executor functions for each drawing tool.

Every executor has the signature:
    (canvas: PIL.Image.Image, args: <ToolArgs>) -> str

The returned string is a human-readable result message that is fed back to
the LLM as the tool-call result.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from app.services.tools.definitions import (
    DitherArgs,
    DrawCircleArgs,
    DrawLineArgs,
    FillRectArgs,
    FloodFillArgs,
    GradientFillArgs,
    MirrorArgs,
    RotateArgs,
    SealCanvasArgs,
    SetPaletteArgs,
    SetPixelArgs,
)


def execute_set_pixel(canvas: Image.Image, args: SetPixelArgs) -> str:
    """Set a single pixel on the canvas."""
    canvas.putpixel((args.x, args.y), (args.r, args.g, args.b))
    return f"Pixel set at ({args.x}, {args.y}) to ({args.r}, {args.g}, {args.b})"


def execute_fill_rect(canvas: Image.Image, args: FillRectArgs) -> str:
    """Fill a rectangular region with a solid color."""
    color = (args.r, args.g, args.b)
    for y in range(args.y1, args.y2 + 1):
        for x in range(args.x1, args.x2 + 1):
            canvas.putpixel((x, y), color)
    width = args.x2 - args.x1 + 1
    height = args.y2 - args.y1 + 1
    return (
        f"Filled rect ({args.x1},{args.y1})-({args.x2},{args.y2}) "
        f"({width}x{height} px) with ({args.r}, {args.g}, {args.b})"
    )


def execute_set_palette(canvas: Image.Image, args: SetPaletteArgs) -> str:
    """Store a palette hint on the canvas.

    The palette is advisory -- it tells the LLM which colours are available
    but does not restrict drawing.  We attach it as an attribute on the
    canvas object so downstream code can inspect it.
    """
    canvas._palette_hint = [tuple(c) for c in args.colors]  # type: ignore[attr-defined]
    return f"Palette set with {len(args.colors)} colors"


def execute_draw_line(canvas: Image.Image, args: DrawLineArgs) -> str:
    """Draw a 1-pixel-wide line between two points."""
    draw = ImageDraw.Draw(canvas)
    color = (args.r, args.g, args.b)
    draw.line([(args.x1, args.y1), (args.x2, args.y2)], fill=color)
    return (
        f"Line drawn from ({args.x1},{args.y1}) to ({args.x2},{args.y2}) "
        f"with color ({args.r}, {args.g}, {args.b})"
    )


def execute_draw_circle(canvas: Image.Image, args: DrawCircleArgs) -> str:
    """Draw a circle (outline or filled) on the canvas."""
    draw = ImageDraw.Draw(canvas)
    color = (args.r, args.g, args.b)
    bbox = [
        args.cx - args.radius,
        args.cy - args.radius,
        args.cx + args.radius,
        args.cy + args.radius,
    ]
    if args.fill:
        draw.ellipse(bbox, fill=color)
    else:
        draw.ellipse(bbox, outline=color)
    fill_str = "filled" if args.fill else "outline"
    return (
        f"Circle ({fill_str}) at ({args.cx},{args.cy}) r={args.radius} "
        f"with color ({args.r}, {args.g}, {args.b})"
    )


def execute_flood_fill(canvas: Image.Image, args: FloodFillArgs) -> str:
    """Flood-fill from a seed point with a new color."""
    new_color = (args.r, args.g, args.b)
    ImageDraw.floodfill(canvas, (args.x, args.y), new_color)
    return (
        f"Flood fill from ({args.x},{args.y}) "
        f"with color ({args.r}, {args.g}, {args.b})"
    )


def execute_gradient_fill(
    canvas: Image.Image, args: GradientFillArgs
) -> str:
    """Fill a rectangular region with a linear gradient between two colors."""
    if args.direction == "horizontal":
        span = max(args.x2 - args.x1, 1)
        for x in range(args.x1, args.x2 + 1):
            t = (x - args.x1) / span
            r = int(args.r1 + (args.r2 - args.r1) * t)
            g = int(args.g1 + (args.g2 - args.g1) * t)
            b = int(args.b1 + (args.b2 - args.b1) * t)
            for y in range(args.y1, args.y2 + 1):
                canvas.putpixel((x, y), (r, g, b))
    else:  # vertical
        span = max(args.y2 - args.y1, 1)
        for y in range(args.y1, args.y2 + 1):
            t = (y - args.y1) / span
            r = int(args.r1 + (args.r2 - args.r1) * t)
            g = int(args.g1 + (args.g2 - args.g1) * t)
            b = int(args.b1 + (args.b2 - args.b1) * t)
            for x in range(args.x1, args.x2 + 1):
                canvas.putpixel((x, y), (r, g, b))
    return (
        f"Gradient ({args.direction}) from ({args.x1},{args.y1}) to "
        f"({args.x2},{args.y2})"
    )


def execute_dither(canvas: Image.Image, args: DitherArgs) -> str:
    """Apply a checkerboard dither pattern between two colors."""
    color1 = (args.r1, args.g1, args.b1)
    color2 = (args.r2, args.g2, args.b2)
    for y in range(args.y1, args.y2 + 1):
        for x in range(args.x1, args.x2 + 1):
            canvas.putpixel((x, y), color1 if (x + y) % 2 == 0 else color2)
    return (
        f"Dither pattern from ({args.x1},{args.y1}) to ({args.x2},{args.y2}) "
        f"between ({args.r1},{args.g1},{args.b1}) and "
        f"({args.r2},{args.g2},{args.b2})"
    )


def execute_mirror(canvas: Image.Image, args: MirrorArgs) -> str:
    """Mirror (flip) the entire canvas along an axis.

    Because PIL's transpose returns a *new* image we paste the result back
    onto the original canvas so mutations are visible to the caller.
    """
    if args.axis == "horizontal":
        flipped = canvas.transpose(Image.FLIP_LEFT_RIGHT)
    else:
        flipped = canvas.transpose(Image.FLIP_TOP_BOTTOM)
    canvas.paste(flipped)
    return f"Canvas mirrored {args.axis}ly"


def execute_rotate(canvas: Image.Image, args: RotateArgs) -> str:
    """Rotate the canvas by a given number of degrees.

    Uses nearest-neighbor resampling (appropriate for pixel art) and
    pastes the result back onto the original canvas object.
    """
    rotated = canvas.rotate(args.degrees, expand=False, fillcolor=(0, 0, 0))
    canvas.paste(rotated)
    return f"Canvas rotated {args.degrees} degrees"


def execute_seal_canvas(canvas: Image.Image, args: SealCanvasArgs) -> str:
    """Mark the canvas as sealed.

    The actual seal bookkeeping is handled by the ToolHarness; this
    executor simply returns the confirmation message.
    """
    return "Canvas sealed"
