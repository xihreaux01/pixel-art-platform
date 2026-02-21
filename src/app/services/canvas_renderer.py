"""Canvas renderer -- manages the PIL Image canvas with checkpoint/restore support.

Provides gzip-compressed PNG serialisation for efficient database storage
of intermediate checkpoints, as well as final PNG export and thumbnail
generation.
"""

from __future__ import annotations

import gzip
import io

from PIL import Image


class CanvasRenderer:
    """Manages the pixel art canvas with checkpoint/restore."""

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.canvas: Image.Image = Image.new("RGB", (width, height), (0, 0, 0))

    def get_canvas(self) -> Image.Image:
        """Return the current canvas image."""
        return self.canvas

    def set_canvas(self, canvas: Image.Image) -> None:
        """Replace the current canvas image."""
        self.canvas = canvas

    def checkpoint(self) -> bytes:
        """Serialize canvas to gzip-compressed PNG bytes for DB storage."""
        buf = io.BytesIO()
        self.canvas.save(buf, format="PNG")
        return gzip.compress(buf.getvalue())

    def restore(self, data: bytes) -> None:
        """Restore canvas from gzip-compressed PNG bytes."""
        raw = gzip.decompress(data)
        self.canvas = Image.open(io.BytesIO(raw)).convert("RGB")

    def to_png_bytes(self) -> bytes:
        """Export canvas as PNG bytes (for final storage)."""
        buf = io.BytesIO()
        self.canvas.save(buf, format="PNG")
        return buf.getvalue()

    def create_thumbnail(self, max_size: tuple[int, int] = (64, 64)) -> bytes:
        """Create a thumbnail PNG."""
        thumb = self.canvas.copy()
        thumb.thumbnail(max_size, Image.Resampling.NEAREST)
        buf = io.BytesIO()
        thumb.save(buf, format="PNG")
        return buf.getvalue()
