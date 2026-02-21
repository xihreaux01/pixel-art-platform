"""HMAC-SHA256 seal with key versioning for art piece provenance.

Creates and verifies tamper-evident signatures that bind image content to
its metadata (art_id, creator_id, model_name).  The seal is computed as::

    HMAC-SHA256(key, "{sha256(image)}:{art_id}:{creator_id}:{model}:{key_ver}")

Key versioning allows key rotation without invalidating existing seals --
each ``ArtPiece`` row stores its ``seal_key_version`` so the correct key
can be selected during verification.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

from app.config import settings


@dataclass(frozen=True)
class SealMetadata:
    """Groups the metadata fields used to compute an authenticity seal."""

    art_id: str
    creator_id: str
    model_name: str
    key_version: int = 1


class AuthenticityManager:
    """Creates and verifies HMAC-SHA256 seals for art piece authenticity."""

    @staticmethod
    def create_seal(
        image_bytes: bytes,
        metadata: SealMetadata,
    ) -> tuple[str, str]:
        """Create an HMAC seal and image hash.

        Returns ``(seal_signature, generation_hash)``.
        """
        # SHA-256 hash of the image
        generation_hash = hashlib.sha256(image_bytes).hexdigest()

        # HMAC seal over combined data
        seal_data = (
            f"{generation_hash}:{metadata.art_id}:{metadata.creator_id}"
            f":{metadata.model_name}:{metadata.key_version}"
        )
        seal_signature = hmac.new(
            settings.HMAC_KEY.encode(),
            seal_data.encode(),
            hashlib.sha256,
        ).hexdigest()

        return seal_signature, generation_hash

    @staticmethod
    def verify_seal(
        image_bytes: bytes,
        metadata: SealMetadata,
        seal_signature: str,
    ) -> bool:
        """Verify an HMAC seal against the image and metadata."""
        expected_sig, _ = AuthenticityManager.create_seal(image_bytes, metadata)
        return hmac.compare_digest(seal_signature, expected_sig)
