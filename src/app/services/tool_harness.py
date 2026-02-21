"""Tool harness -- validates and executes drawing tool calls on a PIL canvas.

The harness enforces a strict validation chain before any drawing function
runs:

1. **Tool name** -- must be a valid ``ToolName`` enum member.
2. **Tier access** -- the tool must appear in the tier's ``allowed_tools``.
3. **Budget** -- ``tool_calls_executed`` must be below ``tool_budget_hard``.
4. **Sealed** -- no calls are accepted once the canvas has been sealed.
5. **Args** -- Pydantic strict validation (``extra="forbid"``).
6. **Bounds** -- all x/y coordinates must fall inside the canvas.
7. **Execute** -- static dispatch table only (no ``getattr`` / ``eval``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PIL import Image
from pydantic import BaseModel, ValidationError

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
    ToolName,
)
from app.services.tools.executors import (
    execute_dither,
    execute_draw_circle,
    execute_draw_line,
    execute_fill_rect,
    execute_flood_fill,
    execute_gradient_fill,
    execute_mirror,
    execute_rotate,
    execute_seal_canvas,
    execute_set_palette,
    execute_set_pixel,
)


# ---------------------------------------------------------------------------
# Result / config data classes
# ---------------------------------------------------------------------------

@dataclass
class ToolCallResult:
    """Outcome of a single tool-call attempt."""

    tool_name: str
    success: bool
    message: str


@dataclass
class HarnessConfig:
    """Configuration passed to the harness at creation time."""

    canvas_width: int
    canvas_height: int
    allowed_tools: list[str]  # from GenerationTierDefinition.allowed_tools
    tool_budget_hard: int


# ---------------------------------------------------------------------------
# Coordinate fields per args model
# ---------------------------------------------------------------------------

# Maps each args class to the attribute names that represent x/y coordinates
# which must be bounds-checked against the canvas dimensions.
_COORD_FIELDS: dict[type[BaseModel], list[tuple[str, str]]] = {
    SetPixelArgs: [("x", "x"), ("y", "y")],
    FillRectArgs: [("x1", "x"), ("y1", "y"), ("x2", "x"), ("y2", "y")],
    DrawLineArgs: [("x1", "x"), ("y1", "y"), ("x2", "x"), ("y2", "y")],
    DrawCircleArgs: [("cx", "x"), ("cy", "y")],
    FloodFillArgs: [("x", "x"), ("y", "y")],
    GradientFillArgs: [("x1", "x"), ("y1", "y"), ("x2", "x"), ("y2", "y")],
    DitherArgs: [("x1", "x"), ("y1", "y"), ("x2", "x"), ("y2", "y")],
}


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

class ToolHarness:
    """Validates and executes tool calls on a PIL canvas.

    The class keeps a static ``DISPATCH`` table that maps each
    ``ToolName`` to a ``(ArgsModel, executor_fn)`` pair.  No dynamic
    dispatch is used -- ``getattr`` and ``eval`` are explicitly avoided.
    """

    # Static dispatch table -- NO getattr, NO eval, NO dynamic lookup.
    DISPATCH: dict[ToolName, tuple[type[BaseModel], Callable[..., str]]] = {
        ToolName.SET_PIXEL: (SetPixelArgs, execute_set_pixel),
        ToolName.FILL_RECT: (FillRectArgs, execute_fill_rect),
        ToolName.SET_PALETTE: (SetPaletteArgs, execute_set_palette),
        ToolName.SEAL_CANVAS: (SealCanvasArgs, execute_seal_canvas),
        ToolName.DRAW_LINE: (DrawLineArgs, execute_draw_line),
        ToolName.DRAW_CIRCLE: (DrawCircleArgs, execute_draw_circle),
        ToolName.FLOOD_FILL: (FloodFillArgs, execute_flood_fill),
        ToolName.GRADIENT_FILL: (GradientFillArgs, execute_gradient_fill),
        ToolName.DITHER: (DitherArgs, execute_dither),
        ToolName.MIRROR: (MirrorArgs, execute_mirror),
        ToolName.ROTATE: (RotateArgs, execute_rotate),
    }

    def __init__(self, config: HarnessConfig) -> None:
        self.config = config
        self.canvas: Image.Image = Image.new(
            "RGB",
            (config.canvas_width, config.canvas_height),
            (0, 0, 0),
        )
        self.tool_calls_executed: int = 0
        self.sealed: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute(self, tool_name_str: str, raw_args: dict[str, Any]) -> ToolCallResult:
        """Validate and execute a single tool call.

        Returns a ``ToolCallResult`` regardless of success or failure so
        callers never need to handle exceptions from this layer.
        """
        # Phase 1 -- pre-flight validation.
        pre_error = self._validate_preconditions(tool_name_str)
        if pre_error is not None:
            return pre_error

        tool_name = ToolName(tool_name_str)
        args_model_cls, executor_fn = self.DISPATCH[tool_name]

        # Phase 2 -- parse and bounds-check arguments.
        args_or_error = self._validate_args(tool_name_str, args_model_cls, raw_args)
        if isinstance(args_or_error, ToolCallResult):
            return args_or_error

        # Phase 3 -- run the executor and record bookkeeping.
        return self._run_and_record(tool_name_str, tool_name, executor_fn, args_or_error)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _validate_preconditions(self, tool_name_str: str) -> ToolCallResult | None:
        """Run gate checks: valid name, seal state, tier access, budget.

        Returns a failure ``ToolCallResult`` if any check fails, or
        ``None`` when all preconditions pass.
        """
        # Tool name must be a known enum member.
        try:
            ToolName(tool_name_str)
        except ValueError:
            return ToolCallResult(
                tool_name=tool_name_str,
                success=False,
                message=f"Unknown tool: {tool_name_str!r}",
            )

        # Canvas must not be sealed.
        if self.sealed:
            return ToolCallResult(
                tool_name=tool_name_str,
                success=False,
                message="Canvas is sealed -- no further tool calls accepted",
            )

        # Tier must allow this tool.
        if tool_name_str not in self.config.allowed_tools:
            return ToolCallResult(
                tool_name=tool_name_str,
                success=False,
                message=f"Tool {tool_name_str!r} is not allowed in the current tier",
            )

        # Budget must not be exhausted.
        if self.tool_calls_executed >= self.config.tool_budget_hard:
            return ToolCallResult(
                tool_name=tool_name_str,
                success=False,
                message=(
                    f"Tool budget exhausted "
                    f"({self.tool_calls_executed}/{self.config.tool_budget_hard})"
                ),
            )

        return None

    def _validate_args(
        self,
        tool_name_str: str,
        args_model_cls: type[BaseModel],
        raw_args: dict[str, Any],
    ) -> BaseModel | ToolCallResult:
        """Parse raw arguments and bounds-check coordinates.

        Returns the validated ``BaseModel`` instance on success, or a
        failure ``ToolCallResult`` when validation fails.
        """
        try:
            args = args_model_cls.model_validate(raw_args)
        except ValidationError as exc:
            return ToolCallResult(
                tool_name=tool_name_str,
                success=False,
                message=f"Argument validation failed: {exc}",
            )

        bounds_error = self._check_bounds(args)
        if bounds_error is not None:
            return ToolCallResult(
                tool_name=tool_name_str,
                success=False,
                message=bounds_error,
            )

        return args

    def _run_and_record(
        self,
        tool_name_str: str,
        tool_name: ToolName,
        executor_fn: Callable[..., str],
        args: BaseModel,
    ) -> ToolCallResult:
        """Execute the tool function and update internal counters."""
        try:
            message = executor_fn(self.canvas, args)
        except Exception as exc:  # pragma: no cover -- defensive
            return ToolCallResult(
                tool_name=tool_name_str,
                success=False,
                message=f"Execution error: {exc}",
            )

        self.tool_calls_executed += 1
        if tool_name is ToolName.SEAL_CANVAS:
            self.sealed = True

        return ToolCallResult(
            tool_name=tool_name_str,
            success=True,
            message=message,
        )

    def _check_bounds(self, args: BaseModel) -> str | None:
        """Return an error message if any coordinate falls outside the canvas.

        Returns ``None`` when all coordinates are valid.
        """
        coord_specs = _COORD_FIELDS.get(type(args))
        if coord_specs is None:
            return None

        w = self.config.canvas_width
        h = self.config.canvas_height

        for attr_name, axis in coord_specs:
            value = getattr(args, attr_name)
            if axis == "x" and value >= w:
                return (
                    f"Coordinate {attr_name}={value} is out of bounds "
                    f"(canvas width={w})"
                )
            if axis == "y" and value >= h:
                return (
                    f"Coordinate {attr_name}={value} is out of bounds "
                    f"(canvas height={h})"
                )

        return None
