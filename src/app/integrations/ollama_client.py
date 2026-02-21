"""Async Ollama client with tool-calling support for pixel art generation.

Uses httpx.AsyncClient to communicate with the Ollama /api/chat endpoint.
Supports multi-turn generation loops where the model repeatedly issues tool
calls until it invokes seal_canvas or hits the iteration/time budget.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import settings
from app.integrations.tool_schemas import TOOL_DESCRIPTIONS, build_tools_schema


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class OllamaToolCall:
    """A single parsed tool call from an Ollama response."""

    name: str
    arguments: dict[str, Any]


@dataclass
class OllamaResponse:
    """Parsed result of an Ollama /api/chat call."""

    tool_calls: list[OllamaToolCall] = field(default_factory=list)
    raw_content: str = ""


@dataclass
class GenerationConfig:
    """Configuration bundle for a pixel-art generation job."""

    canvas_width: int
    canvas_height: int
    allowed_tools: list[str]
    max_iterations: int = 20
    per_call_timeout: float = 60.0
    total_timeout: float = 300.0


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class OllamaTimeoutError(Exception):
    """Raised when a call or job exceeds its time budget."""


class OllamaConnectionError(Exception):
    """Raised when the Ollama server is unreachable."""


class OllamaMalformedResponseError(Exception):
    """Raised when the response cannot be parsed into tool calls."""


# ---------------------------------------------------------------------------
# OllamaClient
# ---------------------------------------------------------------------------

class OllamaClient:
    """Async client for the Ollama /api/chat endpoint with tool-calling."""

    def __init__(self, base_url: str | None = None, model: str = "qwen2.5:7b"):
        self.base_url = (base_url or settings.OLLAMA_URL).rstrip("/")
        self.model = model

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def chat_with_tools(
        self,
        messages: list[dict[str, str]],
        tools: list[dict],
        timeout: float = 120.0,
    ) -> OllamaResponse:
        """Send a chat request with tools and return parsed tool calls.

        Raises:
            OllamaTimeoutError: on request timeout.
            OllamaConnectionError: on connection failure.
            OllamaMalformedResponseError: on unparseable response.
        """
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "stream": False,
        }

        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(timeout),
            ) as client:
                response = await client.post("/api/chat", json=payload)
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise OllamaTimeoutError(
                f"Ollama request timed out after {timeout}s"
            ) from exc
        except httpx.ConnectError as exc:
            raise OllamaConnectionError(
                f"Cannot connect to Ollama at {self.base_url}"
            ) from exc

        return self._parse_response(response.json())

    async def generate_pixel_art(
        self,
        prompt: str,
        config: GenerationConfig,
    ) -> list[OllamaToolCall]:
        """Multi-turn generation loop.

        Keeps calling Ollama until the model calls seal_canvas or the
        iteration / time budget is exhausted.

        Returns all tool calls collected across iterations.
        """
        messages = self._init_messages(prompt, config)
        tools_schema = build_tools_schema(config.allowed_tools)

        all_calls: list[OllamaToolCall] = []
        start = time.monotonic()

        for _ in range(config.max_iterations):
            self._check_total_timeout(start, config.total_timeout)

            result = await self.chat_with_tools(
                messages=messages,
                tools=tools_schema,
                timeout=config.per_call_timeout,
            )
            all_calls.extend(result.tool_calls)

            if any(tc.name == "seal_canvas" for tc in result.tool_calls):
                break

            self._append_turn(messages, result)

        return all_calls

    # ------------------------------------------------------------------
    # Prompt / schema builders
    # ------------------------------------------------------------------

    @staticmethod
    def _build_system_prompt(canvas_width: int, canvas_height: int) -> str:
        """Build the system prompt for pixel art generation."""
        tool_lines = "\n".join(
            f"  - {name}: {desc}" for name, desc in TOOL_DESCRIPTIONS.items()
        )
        return (
            f"You are a pixel art generator. You create pixel art by calling drawing tools.\n"
            f"\n"
            f"Canvas: {canvas_width}x{canvas_height} pixels "
            f"(coordinates 0-{canvas_width - 1} horizontally, "
            f"0-{canvas_height - 1} vertically). Origin (0,0) is top-left.\n"
            f"\n"
            f"Available tools:\n{tool_lines}\n"
            f"\n"
            f"Rules:\n"
            f"1. Create pixel art using ONLY tool calls. Do not output text.\n"
            f"2. Call seal_canvas when the artwork is complete.\n"
            f"3. Stay within canvas bounds.\n"
            f"4. Use multiple tool calls per response when possible.\n"
            f"\n"
            f"Common colors (RGB):\n"
            f"  Red: (255,0,0)  Green: (0,255,0)  Blue: (0,0,255)\n"
            f"  Yellow: (255,255,0)  Cyan: (0,255,255)  Magenta: (255,0,255)\n"
            f"  White: (255,255,255)  Black: (0,0,0)  Orange: (255,165,0)\n"
            f"  Purple: (128,0,128)  Brown: (139,69,19)  Pink: (255,192,203)\n"
            f"  Gray: (128,128,128)  Light Gray: (192,192,192)\n"
        )

    @staticmethod
    def _build_tools_schema(allowed_tools: list[str]) -> list[dict]:
        """Build the Ollama tools schema from allowed tool names."""
        return build_tools_schema(allowed_tools)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _init_messages(
        self, prompt: str, config: GenerationConfig,
    ) -> list[dict[str, str]]:
        """Create the initial message list for a generation job."""
        return [
            {"role": "system", "content": self._build_system_prompt(
                config.canvas_width, config.canvas_height,
            )},
            {"role": "user", "content": prompt},
        ]

    @staticmethod
    def _check_total_timeout(start: float, total_timeout: float) -> None:
        """Raise if the total job timeout has been exceeded."""
        if time.monotonic() - start >= total_timeout:
            raise OllamaTimeoutError(
                f"Total job timeout of {total_timeout}s exceeded"
            )

    @staticmethod
    def _append_turn(
        messages: list[dict[str, str]], result: OllamaResponse,
    ) -> None:
        """Append assistant + follow-up user message for the next turn."""
        messages.append({
            "role": "assistant",
            "content": result.raw_content or "",
        })
        if result.tool_calls:
            summaries = ", ".join(
                f"{tc.name}({tc.arguments})" for tc in result.tool_calls
            )
            messages.append({
                "role": "user",
                "content": f"Tool calls executed: {summaries}. Continue drawing.",
            })
        else:
            messages.append({
                "role": "user",
                "content": "Please use the available tools to draw. "
                           "Call seal_canvas when done.",
            })

    @staticmethod
    def _parse_response(data: dict) -> OllamaResponse:
        """Parse the JSON body from Ollama /api/chat into an OllamaResponse."""
        message = data.get("message")
        if not isinstance(message, dict):
            raise OllamaMalformedResponseError(
                "Response missing 'message' key or it is not a dict"
            )

        raw_content = message.get("content", "")
        raw_tool_calls = message.get("tool_calls")
        parsed: list[OllamaToolCall] = []

        if raw_tool_calls is not None:
            if not isinstance(raw_tool_calls, list):
                raise OllamaMalformedResponseError(
                    "'tool_calls' is not a list"
                )
            for tc in raw_tool_calls:
                func = tc.get("function", {})
                name = func.get("name")
                arguments = func.get("arguments", {})
                if not name:
                    raise OllamaMalformedResponseError(
                        "Tool call missing function name"
                    )
                parsed.append(OllamaToolCall(name=name, arguments=arguments))

        return OllamaResponse(tool_calls=parsed, raw_content=raw_content)
