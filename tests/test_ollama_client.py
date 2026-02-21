"""Tests for the Ollama integration client.

All HTTP calls are mocked -- no real Ollama server is required.

Run with:
    ./venv/bin/python -m pytest tests/test_ollama_client.py -v
"""

from __future__ import annotations

import httpx
import pytest
from unittest.mock import AsyncMock, patch

from app.integrations.ollama_client import (
    GenerationConfig,
    OllamaClient,
    OllamaConnectionError,
    OllamaMalformedResponseError,
    OllamaTimeoutError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ollama_response(tool_calls: list[dict] | None = None, content: str = ""):
    """Build a fake Ollama /api/chat JSON body."""
    msg: dict = {"role": "assistant", "content": content}
    if tool_calls is not None:
        msg["tool_calls"] = tool_calls
    return {"message": msg}


def _tool_call(name: str, arguments: dict | None = None) -> dict:
    """Build a single tool_call entry in Ollama format."""
    return {"function": {"name": name, "arguments": arguments or {}}}


def _mock_post_response(json_body: dict, status_code: int = 200):
    """Return an httpx.Response suitable for mocking."""
    return httpx.Response(
        status_code=status_code,
        json=json_body,
        request=httpx.Request("POST", "http://test/api/chat"),
    )


def _default_config(**overrides) -> GenerationConfig:
    defaults = dict(
        canvas_width=16,
        canvas_height=16,
        allowed_tools=["set_pixel", "fill_rect", "seal_canvas"],
        max_iterations=20,
        per_call_timeout=60.0,
        total_timeout=300.0,
    )
    defaults.update(overrides)
    return GenerationConfig(**defaults)


# ---------------------------------------------------------------------------
# 1. test_chat_with_tools_success
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_with_tools_success():
    """A valid Ollama response with tool_calls is parsed correctly."""
    body = _ollama_response(tool_calls=[
        _tool_call("set_pixel", {"x": 0, "y": 0, "r": 255, "g": 0, "b": 0}),
        _tool_call("fill_rect", {"x1": 1, "y1": 1, "x2": 3, "y2": 3, "r": 0, "g": 255, "b": 0}),
    ])

    client = OllamaClient(base_url="http://fake:11434")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = _mock_post_response(body)
        result = await client.chat_with_tools(
            messages=[{"role": "user", "content": "draw something"}],
            tools=[],
        )

    assert len(result.tool_calls) == 2
    assert result.tool_calls[0].name == "set_pixel"
    assert result.tool_calls[0].arguments["r"] == 255
    assert result.tool_calls[1].name == "fill_rect"


# ---------------------------------------------------------------------------
# 2. test_chat_with_tools_timeout
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_with_tools_timeout():
    """A timeout from httpx is wrapped as OllamaTimeoutError."""
    client = OllamaClient(base_url="http://fake:11434")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.ReadTimeout("timed out")

        with pytest.raises(OllamaTimeoutError, match="timed out"):
            await client.chat_with_tools(
                messages=[{"role": "user", "content": "hi"}],
                tools=[],
                timeout=1.0,
            )


# ---------------------------------------------------------------------------
# 3. test_chat_with_tools_malformed_response
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_with_tools_malformed_response():
    """A response without a proper 'message' dict raises MalformedResponseError."""
    body = {"not_message": "oops"}
    client = OllamaClient(base_url="http://fake:11434")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = _mock_post_response(body)

        with pytest.raises(OllamaMalformedResponseError, match="message"):
            await client.chat_with_tools(
                messages=[{"role": "user", "content": "hi"}],
                tools=[],
            )


# ---------------------------------------------------------------------------
# 4. test_chat_with_tools_connection_error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_with_tools_connection_error():
    """A connection refusal is wrapped as OllamaConnectionError."""
    client = OllamaClient(base_url="http://fake:11434")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.ConnectError("Connection refused")

        with pytest.raises(OllamaConnectionError, match="Cannot connect"):
            await client.chat_with_tools(
                messages=[{"role": "user", "content": "hi"}],
                tools=[],
            )


# ---------------------------------------------------------------------------
# 5. test_generate_pixel_art_single_iteration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_pixel_art_single_iteration():
    """When the first response contains seal_canvas, the loop stops after 1 iteration."""
    body = _ollama_response(tool_calls=[
        _tool_call("set_pixel", {"x": 0, "y": 0, "r": 255, "g": 0, "b": 0}),
        _tool_call("seal_canvas"),
    ])

    client = OllamaClient(base_url="http://fake:11434")
    config = _default_config()

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = _mock_post_response(body)

        calls = await client.generate_pixel_art("draw red dot", config)

    # Only one HTTP call should have been made.
    assert mock_post.call_count == 1
    assert len(calls) == 2
    assert calls[0].name == "set_pixel"
    assert calls[1].name == "seal_canvas"


# ---------------------------------------------------------------------------
# 6. test_generate_pixel_art_multi_iteration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_pixel_art_multi_iteration():
    """Two iterations: first draws, second seals. All tool calls accumulated."""
    first = _mock_post_response(_ollama_response(tool_calls=[
        _tool_call("set_pixel", {"x": 0, "y": 0, "r": 255, "g": 0, "b": 0}),
    ]))
    second = _mock_post_response(_ollama_response(tool_calls=[
        _tool_call("fill_rect", {"x1": 1, "y1": 1, "x2": 3, "y2": 3, "r": 0, "g": 0, "b": 255}),
        _tool_call("seal_canvas"),
    ]))

    client = OllamaClient(base_url="http://fake:11434")
    config = _default_config()

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [first, second]

        calls = await client.generate_pixel_art("draw something", config)

    assert mock_post.call_count == 2
    assert len(calls) == 3
    assert calls[0].name == "set_pixel"
    assert calls[1].name == "fill_rect"
    assert calls[2].name == "seal_canvas"


# ---------------------------------------------------------------------------
# 7. test_generate_pixel_art_max_iterations
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_pixel_art_max_iterations():
    """When the model never seals, the loop stops at max_iterations."""
    body = _ollama_response(tool_calls=[
        _tool_call("set_pixel", {"x": 0, "y": 0, "r": 0, "g": 0, "b": 0}),
    ])

    client = OllamaClient(base_url="http://fake:11434")
    config = _default_config(max_iterations=3)

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = _mock_post_response(body)

        calls = await client.generate_pixel_art("draw forever", config)

    assert mock_post.call_count == 3
    # Each iteration produces 1 set_pixel call.
    assert len(calls) == 3
    assert all(c.name == "set_pixel" for c in calls)


# ---------------------------------------------------------------------------
# 8. test_build_tools_schema
# ---------------------------------------------------------------------------

def test_build_tools_schema():
    """Schema is built only for the requested tool names."""
    client = OllamaClient(base_url="http://fake:11434")

    schema = client._build_tools_schema(["set_pixel", "seal_canvas"])
    assert len(schema) == 2

    names = {entry["function"]["name"] for entry in schema}
    assert names == {"set_pixel", "seal_canvas"}

    # Unknown tools are silently excluded.
    schema2 = client._build_tools_schema(["set_pixel", "nonexistent"])
    assert len(schema2) == 1
    assert schema2[0]["function"]["name"] == "set_pixel"
