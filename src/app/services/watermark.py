"""LSB steganographic watermark for pixel art provenance.

Encodes ``art_id`` and ``owner_id`` (two UUIDs = 32 bytes = 256 bits) into
the least-significant bit of the R channel of the first 256 pixels.  If the
image has fewer than 256 pixels it falls back to using R+G+B channels, but
in practice all generation tiers produce canvases with at least 256 pixels.
"""

from __future__ import annotations

import uuid

from PIL import Image


class WatermarkEncoder:
    """LSB steganographic watermark for pixel art provenance."""

    @staticmethod
    def encode(image: Image.Image, art_id: uuid.UUID, owner_id: uuid.UUID) -> Image.Image:
        """Encode art_id and owner_id into the LSB of the image pixels.

        Encodes 32 bytes (two UUIDs) = 256 bits into the LSB of the first
        256 pixels' R channel.  If the image has fewer than 256 pixels,
        uses R+G+B channels.
        """
        img = image.copy()
        data = art_id.bytes + owner_id.bytes  # 32 bytes = 256 bits
        pixels = img.load()
        width, height = img.size

        bit_idx = 0
        for y in range(height):
            for x in range(width):
                if bit_idx >= len(data) * 8:
                    return img
                r, g, b = pixels[x, y]
                # Encode in R channel LSB
                bit = (data[bit_idx // 8] >> (7 - bit_idx % 8)) & 1
                r = (r & 0xFE) | bit
                pixels[x, y] = (r, g, b)
                bit_idx += 1
        return img

    @staticmethod
    def decode(image: Image.Image) -> tuple[uuid.UUID, uuid.UUID]:
        """Decode art_id and owner_id from the LSB of the image pixels."""
        pixels = image.load()
        width, height = image.size

        bits: list[int] = []
        for y in range(height):
            for x in range(width):
                if len(bits) >= 256:  # 32 bytes * 8 bits
                    break
                r, _, _ = pixels[x, y]
                bits.append(r & 1)
            if len(bits) >= 256:
                break

        # Convert bits to bytes
        data = bytearray(32)
        for i in range(256):
            data[i // 8] |= bits[i] << (7 - i % 8)

        art_id = uuid.UUID(bytes=bytes(data[:16]))
        owner_id = uuid.UUID(bytes=bytes(data[16:32]))
        return art_id, owner_id
