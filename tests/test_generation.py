"""Tests for canvas renderer, watermark, authenticity, and generation orchestrator.

Run with:
    ./venv/bin/python -m pytest tests/test_generation.py -v
"""

from __future__ import annotations

import io
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from app.services.canvas_renderer import CanvasRenderer
from app.services.watermark import WatermarkEncoder
from app.services.authenticity import AuthenticityManager, SealMetadata


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_test_image(width: int = 32, height: int = 32) -> Image.Image:
    """Create a test image with some non-trivial pixel data."""
    img = Image.new("RGB", (width, height), (0, 0, 0))
    pixels = img.load()
    for y in range(height):
        for x in range(width):
            pixels[x, y] = ((x * 7) % 256, (y * 13) % 256, ((x + y) * 3) % 256)
    return img


def _image_to_bytes(img: Image.Image) -> bytes:
    """Convert a PIL Image to PNG bytes."""
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 1. test_canvas_renderer_checkpoint_restore
# ---------------------------------------------------------------------------

class TestCanvasRendererCheckpointRestore:
    def test_checkpoint_restore_pixels_match(self):
        renderer = CanvasRenderer(16, 16)
        # Draw something on the canvas
        canvas = renderer.get_canvas()
        pixels = canvas.load()
        pixels[5, 5] = (255, 0, 0)
        pixels[10, 10] = (0, 255, 0)

        # Checkpoint
        data = renderer.checkpoint()
        assert isinstance(data, bytes)
        assert len(data) > 0

        # Create a new renderer and restore
        renderer2 = CanvasRenderer(16, 16)
        renderer2.restore(data)

        restored = renderer2.get_canvas()
        assert restored.getpixel((5, 5)) == (255, 0, 0)
        assert restored.getpixel((10, 10)) == (0, 255, 0)
        assert restored.getpixel((0, 0)) == (0, 0, 0)


# ---------------------------------------------------------------------------
# 2. test_canvas_renderer_thumbnail
# ---------------------------------------------------------------------------

class TestCanvasRendererThumbnail:
    def test_thumbnail_is_smaller_than_original(self):
        renderer = CanvasRenderer(128, 128)
        canvas = renderer.get_canvas()
        # Fill with some data so PNG isn't trivially small
        pixels = canvas.load()
        for y in range(128):
            for x in range(128):
                pixels[x, y] = (x % 256, y % 256, (x + y) % 256)

        original_bytes = renderer.to_png_bytes()
        thumbnail_bytes = renderer.create_thumbnail(max_size=(32, 32))

        # Parse the thumbnail back to verify its dimensions
        thumb = Image.open(io.BytesIO(thumbnail_bytes))
        assert thumb.size[0] <= 32
        assert thumb.size[1] <= 32
        assert thumb.size[0] < 128 or thumb.size[1] < 128


# ---------------------------------------------------------------------------
# 3. test_watermark_encode_decode_roundtrip
# ---------------------------------------------------------------------------

class TestWatermarkRoundtrip:
    def test_encode_decode_roundtrip(self):
        art_id = uuid.uuid4()
        owner_id = uuid.uuid4()
        original = _make_test_image(32, 32)

        watermarked = WatermarkEncoder.encode(original, art_id, owner_id)
        decoded_art_id, decoded_owner_id = WatermarkEncoder.decode(watermarked)

        assert decoded_art_id == art_id
        assert decoded_owner_id == owner_id


# ---------------------------------------------------------------------------
# 4. test_watermark_survives_no_modification
# ---------------------------------------------------------------------------

class TestWatermarkSurvivesNoModification:
    def test_only_lsb_changed(self):
        art_id = uuid.uuid4()
        owner_id = uuid.uuid4()
        original = _make_test_image(32, 32)

        watermarked = WatermarkEncoder.encode(original, art_id, owner_id)

        # Check that only the LSB of the R channel differs (at most)
        orig_pixels = original.load()
        wm_pixels = watermarked.load()
        for y in range(32):
            for x in range(32):
                r_o, g_o, b_o = orig_pixels[x, y]
                r_w, g_w, b_w = wm_pixels[x, y]
                # G and B channels must be untouched
                assert g_o == g_w, f"G channel changed at ({x},{y})"
                assert b_o == b_w, f"B channel changed at ({x},{y})"
                # R channel can only differ in the LSB
                assert abs(r_o - r_w) <= 1, f"R channel changed by more than LSB at ({x},{y})"

        # Decode should still work
        decoded_art_id, decoded_owner_id = WatermarkEncoder.decode(watermarked)
        assert decoded_art_id == art_id
        assert decoded_owner_id == owner_id


# ---------------------------------------------------------------------------
# 5. test_authenticity_create_verify
# ---------------------------------------------------------------------------

class TestAuthenticityCreateVerify:
    def test_create_and_verify(self):
        image_bytes = _image_to_bytes(_make_test_image())
        metadata = SealMetadata(
            art_id=str(uuid.uuid4()),
            creator_id=str(uuid.uuid4()),
            model_name="test-model",
        )

        with patch("app.services.authenticity.settings") as mock_settings:
            mock_settings.HMAC_KEY = "test-hmac-key-for-testing"
            seal_sig, gen_hash = AuthenticityManager.create_seal(image_bytes, metadata)

            assert isinstance(seal_sig, str)
            assert isinstance(gen_hash, str)
            assert len(seal_sig) == 64  # SHA-256 hex
            assert len(gen_hash) == 64

            verified = AuthenticityManager.verify_seal(image_bytes, metadata, seal_sig)
            assert verified is True


# ---------------------------------------------------------------------------
# 6. test_authenticity_tampered_image
# ---------------------------------------------------------------------------

class TestAuthenticityTamperedImage:
    def test_tampered_image_fails_verification(self):
        original = _make_test_image()
        image_bytes = _image_to_bytes(original)
        metadata = SealMetadata(
            art_id=str(uuid.uuid4()),
            creator_id=str(uuid.uuid4()),
            model_name="test-model",
        )

        with patch("app.services.authenticity.settings") as mock_settings:
            mock_settings.HMAC_KEY = "test-hmac-key-for-testing"
            seal_sig, _ = AuthenticityManager.create_seal(image_bytes, metadata)

            # Tamper with the image
            tampered = original.copy()
            tampered.putpixel((0, 0), (255, 255, 255))
            tampered_bytes = _image_to_bytes(tampered)

            verified = AuthenticityManager.verify_seal(tampered_bytes, metadata, seal_sig)
            assert verified is False


# ---------------------------------------------------------------------------
# 7. test_authenticity_wrong_metadata
# ---------------------------------------------------------------------------

class TestAuthenticityWrongMetadata:
    def test_wrong_art_id_fails_verification(self):
        image_bytes = _image_to_bytes(_make_test_image())
        metadata_a = SealMetadata(
            art_id=str(uuid.uuid4()),
            creator_id=str(uuid.uuid4()),
            model_name="test-model",
        )
        metadata_b = SealMetadata(
            art_id=str(uuid.uuid4()),
            creator_id=metadata_a.creator_id,
            model_name="test-model",
        )

        with patch("app.services.authenticity.settings") as mock_settings:
            mock_settings.HMAC_KEY = "test-hmac-key-for-testing"
            seal_sig, _ = AuthenticityManager.create_seal(image_bytes, metadata_a)

            verified = AuthenticityManager.verify_seal(image_bytes, metadata_b, seal_sig)
            assert verified is False


# ---------------------------------------------------------------------------
# Orchestrator test helpers
# ---------------------------------------------------------------------------

def _make_tier_config():
    """Create a test TierConfig."""
    from app.services.generation_orchestrator import TierConfig
    return TierConfig(
        canvas_width=16,
        canvas_height=16,
        credit_cost=10,
        tool_budget_soft=80,
        tool_budget_hard=100,
        job_timeout_seconds=300,
        allowed_tools=["set_pixel", "fill_rect", "seal_canvas"],
    )


def _make_job_context(tier=None, **overrides):
    """Create a test JobContext."""
    from app.services.generation_orchestrator import JobContext
    defaults = dict(
        job_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        tier_name="basic",
        tier=tier or _make_tier_config(),
    )
    defaults.update(overrides)
    return JobContext(**defaults)


def _mock_db_row(values):
    """Create a mock DB row that supports index access."""
    mock = MagicMock()
    mock.__getitem__ = lambda self, idx: values[idx]
    return mock


def _make_db_session():
    """Create a mock async DB session."""
    db = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


def _make_redis():
    """Create a mock async Redis client."""
    redis = AsyncMock()
    redis.publish = AsyncMock()
    return redis


def _make_ollama_client(tool_calls=None):
    """Create a mock Ollama client returning predefined tool calls."""
    client = AsyncMock()
    client.generate_pixel_art = AsyncMock(return_value=tool_calls or [])
    return client


# ---------------------------------------------------------------------------
# 8. test_orchestrator_state_transitions
# ---------------------------------------------------------------------------

class TestOrchestratorStateTransitions:
    @pytest.mark.asyncio
    async def test_pending_to_complete(self):
        from app.services.generation_orchestrator import GenerationOrchestrator

        job_id = uuid.uuid4()
        user_id = uuid.uuid4()

        db = _make_db_session()
        redis = _make_redis()

        # Mock load_job: returns a pending job
        job_row = _mock_db_row([job_id, user_id, "basic", "pending"])
        # Mock load_tier: returns tier config
        tier_row = _mock_db_row([16, 16, 10, 80, 100, 300, ["set_pixel", "fill_rect", "seal_canvas"]])

        execute_results = [job_row, tier_row]
        call_count = 0

        async def mock_execute(query, params=None):
            nonlocal call_count
            result = MagicMock()
            if call_count < len(execute_results):
                result.fetchone = MagicMock(return_value=execute_results[call_count])
                call_count += 1
            else:
                result.fetchone = MagicMock(return_value=None)
            return result

        db.execute = AsyncMock(side_effect=mock_execute)

        # Ollama returns a simple set_pixel then seal_canvas
        tool_calls = [
            {"tool_name": "set_pixel", "arguments": {"x": 0, "y": 0, "r": 255, "g": 0, "b": 0}},
            {"tool_name": "seal_canvas", "arguments": {}},
        ]
        ollama = _make_ollama_client(tool_calls)

        orchestrator = GenerationOrchestrator(db=db, redis=redis, ollama=ollama)

        # Patch file system operations
        with patch("app.services.generation_orchestrator.ART_STORAGE_DIR") as mock_dir, \
             patch("app.services.authenticity.settings") as mock_settings:
            mock_settings.HMAC_KEY = "test-key"
            mock_dir.mkdir = MagicMock()
            mock_dir.__truediv__ = lambda self, name: MagicMock(write_bytes=MagicMock())

            await orchestrator.run(job_id)

        # Verify state transitions happened via DB calls
        # The db.execute should have been called multiple times for status updates
        assert db.execute.call_count > 0
        assert db.commit.call_count > 0

        # Verify Ollama was called
        ollama.generate_pixel_art.assert_called_once()

        # Verify Redis progress events were published
        assert redis.publish.call_count > 0


# ---------------------------------------------------------------------------
# 9. test_orchestrator_failure_refund
# ---------------------------------------------------------------------------

class TestOrchestratorFailureRefund:
    @pytest.mark.asyncio
    async def test_failure_triggers_refund(self):
        from app.services.generation_orchestrator import GenerationOrchestrator

        job_id = uuid.uuid4()
        user_id = uuid.uuid4()

        db = _make_db_session()
        redis = _make_redis()

        # Mock load_job: returns a pending job
        job_row = _mock_db_row([job_id, user_id, "basic", "pending"])
        tier_row = _mock_db_row([16, 16, 10, 80, 100, 300, ["set_pixel", "seal_canvas"]])

        execute_results = [job_row, tier_row]
        call_count = 0

        async def mock_execute(query, params=None):
            nonlocal call_count
            result = MagicMock()
            if call_count < len(execute_results):
                result.fetchone = MagicMock(return_value=execute_results[call_count])
                call_count += 1
            else:
                result.fetchone = MagicMock(return_value=None)
            return result

        db.execute = AsyncMock(side_effect=mock_execute)

        # Ollama raises an error
        ollama = _make_ollama_client()
        ollama.generate_pixel_art = AsyncMock(side_effect=RuntimeError("LLM connection failed"))

        orchestrator = GenerationOrchestrator(db=db, redis=redis, ollama=ollama)

        with patch("app.services.generation_orchestrator.refund_credits", new_callable=AsyncMock) as mock_refund:
            mock_refund.return_value = 100
            await orchestrator.run(job_id)

            # Verify refund was attempted
            mock_refund.assert_called_once()
            refund_args = mock_refund.call_args
            assert refund_args[1].get("user_id", refund_args[0][1] if len(refund_args[0]) > 1 else None) == user_id or \
                   refund_args[0][1] == user_id

        # Verify failure was published to Redis
        published_calls = redis.publish.call_args_list
        assert len(published_calls) > 0


# ---------------------------------------------------------------------------
# 10. test_orchestrator_checkpoint_interval
# ---------------------------------------------------------------------------

class TestOrchestratorCheckpointInterval:
    @pytest.mark.asyncio
    async def test_checkpoint_called_every_50_tool_calls(self):
        from app.services.generation_orchestrator import GenerationOrchestrator, CHECKPOINT_INTERVAL

        job_id = uuid.uuid4()
        user_id = uuid.uuid4()

        db = _make_db_session()
        redis = _make_redis()

        job_row = _mock_db_row([job_id, user_id, "basic", "pending"])
        tier_row = _mock_db_row([16, 16, 10, 80, 200, 300, ["set_pixel", "seal_canvas"]])

        execute_results = [job_row, tier_row]
        call_count = 0

        async def mock_execute(query, params=None):
            nonlocal call_count
            result = MagicMock()
            if call_count < len(execute_results):
                result.fetchone = MagicMock(return_value=execute_results[call_count])
                call_count += 1
            else:
                result.fetchone = MagicMock(return_value=None)
            return result

        db.execute = AsyncMock(side_effect=mock_execute)

        # Generate 120 set_pixel calls then a seal_canvas
        tool_calls = []
        for i in range(120):
            tool_calls.append({
                "tool_name": "set_pixel",
                "arguments": {"x": i % 16, "y": i // 16, "r": 100, "g": 100, "b": 100},
            })
        tool_calls.append({"tool_name": "seal_canvas", "arguments": {}})

        ollama = _make_ollama_client(tool_calls)

        orchestrator = GenerationOrchestrator(db=db, redis=redis, ollama=ollama)

        with patch("app.services.generation_orchestrator.ART_STORAGE_DIR") as mock_dir, \
             patch("app.services.authenticity.settings") as mock_settings:
            mock_settings.HMAC_KEY = "test-key"
            mock_dir.mkdir = MagicMock()
            mock_dir.__truediv__ = lambda self, name: MagicMock(write_bytes=MagicMock())

            await orchestrator.run(job_id)

        # With 120 tool calls + 1 seal, checkpoints should happen at call 50 and 100
        # Find checkpoint-related DB calls (those with 'checkpoint_canvas' param)
        checkpoint_calls = []
        for c in db.execute.call_args_list:
            args = c[0] if c[0] else []
            kwargs = c[1] if len(c) > 1 else {}
            params = kwargs.get("params", c[0][1] if len(c[0]) > 1 else {})
            if isinstance(params, dict) and "canvas" in params:
                checkpoint_calls.append(params)

        # We expect 2 checkpoints: at 50 and at 100
        assert len(checkpoint_calls) == 2
        assert checkpoint_calls[0]["idx"] == CHECKPOINT_INTERVAL
        assert checkpoint_calls[1]["idx"] == CHECKPOINT_INTERVAL * 2
