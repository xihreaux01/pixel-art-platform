"""
PoC: Validate Ollama tool-calling for pixel art generation.

Tests whether local LLMs can produce recognizable 16x16 pixel art
by iteratively calling drawing tools on a Pillow canvas.
"""

import json
import time
import httpx
from PIL import Image
from pathlib import Path
from dataclasses import dataclass, field

OLLAMA_URL = "http://localhost:11434"
OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# ── Tool Definitions (Ollama format) ────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "set_pixel",
            "description": "Set a single pixel to an RGB color. Use for fine details.",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "X coordinate (0-15)"},
                    "y": {"type": "integer", "description": "Y coordinate (0-15)"},
                    "r": {"type": "integer", "description": "Red (0-255)"},
                    "g": {"type": "integer", "description": "Green (0-255)"},
                    "b": {"type": "integer", "description": "Blue (0-255)"},
                },
                "required": ["x", "y", "r", "g", "b"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fill_rect",
            "description": "Fill a rectangle with an RGB color. Use for backgrounds and large areas.",
            "parameters": {
                "type": "object",
                "properties": {
                    "x1": {"type": "integer", "description": "Left X (0-15)"},
                    "y1": {"type": "integer", "description": "Top Y (0-15)"},
                    "x2": {"type": "integer", "description": "Right X (0-15)"},
                    "y2": {"type": "integer", "description": "Bottom Y (0-15)"},
                    "r": {"type": "integer", "description": "Red (0-255)"},
                    "g": {"type": "integer", "description": "Green (0-255)"},
                    "b": {"type": "integer", "description": "Blue (0-255)"},
                },
                "required": ["x1", "y1", "x2", "y2", "r", "g", "b"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "draw_line",
            "description": "Draw a 1-pixel line between two points.",
            "parameters": {
                "type": "object",
                "properties": {
                    "x1": {"type": "integer", "description": "Start X (0-15)"},
                    "y1": {"type": "integer", "description": "Start Y (0-15)"},
                    "x2": {"type": "integer", "description": "End X (0-15)"},
                    "y2": {"type": "integer", "description": "End Y (0-15)"},
                    "r": {"type": "integer", "description": "Red (0-255)"},
                    "g": {"type": "integer", "description": "Green (0-255)"},
                    "b": {"type": "integer", "description": "Blue (0-255)"},
                },
                "required": ["x1", "y1", "x2", "y2", "r", "g", "b"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "seal_canvas",
            "description": "Call this when the artwork is complete. No more drawing after this.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]

SYSTEM_PROMPT = """You are a pixel art generator. You create 16x16 pixel art by calling drawing tools.

CANVAS: 16x16 pixels (coordinates 0-15 for both x and y). Origin (0,0) is top-left.
The canvas starts as all black (0,0,0).

AVAILABLE TOOLS:
- set_pixel(x, y, r, g, b): Set one pixel
- fill_rect(x1, y1, x2, y2, r, g, b): Fill a rectangle
- draw_line(x1, y1, x2, y2, r, g, b): Draw a line
- seal_canvas(): Call when artwork is complete

INSTRUCTIONS:
1. Plan the artwork mentally first
2. Start with large areas (fill_rect for backgrounds)
3. Add details with set_pixel and draw_line
4. Use vibrant, contrasting colors for pixel art style
5. Call seal_canvas() when done
6. Stay within bounds (0-15 for all coordinates)
7. Make each tool call count — be deliberate with your pixel placement"""


@dataclass
class TestResult:
    model: str
    prompt: str
    total_tool_calls: int = 0
    valid_tool_calls: int = 0
    invalid_tool_calls: int = 0
    sealed: bool = False
    duration_seconds: float = 0
    error: str | None = None
    tool_breakdown: dict = field(default_factory=dict)


def clamp(val: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, val))


def execute_tool(canvas: Image.Image, name: str, args: dict) -> tuple[bool, str]:
    """Execute a tool call on the canvas. Returns (success, message)."""
    try:
        if name == "set_pixel":
            x = clamp(int(args["x"]), 0, 15)
            y = clamp(int(args["y"]), 0, 15)
            r = clamp(int(args["r"]), 0, 255)
            g = clamp(int(args["g"]), 0, 255)
            b = clamp(int(args["b"]), 0, 255)
            canvas.putpixel((x, y), (r, g, b))
            return True, f"Pixel set at ({x},{y}) = ({r},{g},{b})"

        elif name == "fill_rect":
            x1 = clamp(int(args["x1"]), 0, 15)
            y1 = clamp(int(args["y1"]), 0, 15)
            x2 = clamp(int(args["x2"]), 0, 15)
            y2 = clamp(int(args["y2"]), 0, 15)
            r = clamp(int(args["r"]), 0, 255)
            g = clamp(int(args["g"]), 0, 255)
            b = clamp(int(args["b"]), 0, 255)
            for px in range(min(x1, x2), max(x1, x2) + 1):
                for py in range(min(y1, y2), max(y1, y2) + 1):
                    canvas.putpixel((px, py), (r, g, b))
            return True, f"Rect filled ({x1},{y1})-({x2},{y2}) = ({r},{g},{b})"

        elif name == "draw_line":
            x1 = clamp(int(args["x1"]), 0, 15)
            y1 = clamp(int(args["y1"]), 0, 15)
            x2 = clamp(int(args["x2"]), 0, 15)
            y2 = clamp(int(args["y2"]), 0, 15)
            r = clamp(int(args["r"]), 0, 255)
            g = clamp(int(args["g"]), 0, 255)
            b = clamp(int(args["b"]), 0, 255)
            from PIL import ImageDraw
            draw = ImageDraw.Draw(canvas)
            draw.line([(x1, y1), (x2, y2)], fill=(r, g, b))
            return True, f"Line ({x1},{y1})-({x2},{y2}) = ({r},{g},{b})"

        elif name == "seal_canvas":
            return True, "Canvas sealed"

        else:
            return False, f"Unknown tool: {name}"

    except Exception as e:
        return False, f"Tool error: {e}"


def run_generation(model: str, prompt: str, max_turns: int = 80) -> TestResult:
    """Run a full tool-calling generation loop with one model."""
    result = TestResult(model=model, prompt=prompt)
    canvas = Image.new("RGB", (16, 16), (0, 0, 0))

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Create pixel art of: {prompt}"},
    ]

    client = httpx.Client(timeout=120.0)
    start = time.time()

    try:
        for turn in range(max_turns):
            resp = client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": model,
                    "messages": messages,
                    "tools": TOOLS,
                    "stream": False,
                },
            )
            resp.raise_for_status()
            data = resp.json()

            msg = data.get("message", {})
            tool_calls = msg.get("tool_calls", [])

            # No tool calls = model is done or confused
            if not tool_calls:
                content = msg.get("content", "")
                if content:
                    print(f"  [{model}] Turn {turn}: text response (no tools): {content[:100]}")
                # Add assistant message and break
                messages.append(msg)
                break

            # Process tool calls
            messages.append(msg)

            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "unknown")
                args = fn.get("arguments", {})

                result.total_tool_calls += 1
                result.tool_breakdown[name] = result.tool_breakdown.get(name, 0) + 1

                success, message = execute_tool(canvas, name, args)
                if success:
                    result.valid_tool_calls += 1
                else:
                    result.invalid_tool_calls += 1

                print(f"  [{model}] Turn {turn}: {name}({json.dumps(args)}) → {message}")

                # Feed tool result back
                messages.append({
                    "role": "tool",
                    "content": message,
                })

                if name == "seal_canvas":
                    result.sealed = True

            if result.sealed:
                break

    except Exception as e:
        result.error = str(e)
        print(f"  [{model}] ERROR: {e}")
    finally:
        result.duration_seconds = time.time() - start
        client.close()

    # Save output
    safe_prompt = prompt.replace(" ", "_")[:20]
    out_path = OUTPUT_DIR / f"{model.replace(':', '_')}_{safe_prompt}.png"
    # Scale up for visibility (16x16 → 256x256)
    canvas_scaled = canvas.resize((256, 256), Image.NEAREST)
    canvas_scaled.save(out_path)
    # Also save raw 16x16
    canvas.save(OUTPUT_DIR / f"{model.replace(':', '_')}_{safe_prompt}_raw.png")
    print(f"  [{model}] Saved to {out_path}")

    return result


def main():
    models = ["llama3.1:8b", "qwen2.5:7b", "qwen3:8b"]
    prompts = ["a red mushroom", "a blue sword", "a green tree"]

    # Check Ollama is running
    try:
        httpx.get(f"{OLLAMA_URL}/api/tags", timeout=5.0)
    except httpx.ConnectError:
        print("ERROR: Ollama is not running. Start with: ollama serve")
        return

    results: list[TestResult] = []

    for model in models:
        print(f"\n{'='*60}")
        print(f"Testing: {model}")
        print(f"{'='*60}")

        for prompt in prompts:
            print(f"\n  Prompt: '{prompt}'")
            r = run_generation(model, prompt)
            results.append(r)
            print(f"  Result: {r.valid_tool_calls} valid / {r.total_tool_calls} total calls, "
                  f"sealed={r.sealed}, {r.duration_seconds:.1f}s")
            print(f"  Breakdown: {r.tool_breakdown}")

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"{'Model':<20} {'Prompt':<20} {'Valid/Total':<12} {'Sealed':<8} {'Time':<8} {'Error'}")
    print("-" * 80)
    for r in results:
        err = r.error[:30] if r.error else "-"
        print(f"{r.model:<20} {r.prompt:<20} {r.valid_tool_calls}/{r.total_tool_calls:<10} "
              f"{'YES' if r.sealed else 'NO':<8} {r.duration_seconds:<8.1f} {err}")

    # Write findings
    findings = OUTPUT_DIR / "findings.md"
    with open(findings, "w") as f:
        f.write("# PoC Findings: Ollama Tool-Calling for Pixel Art\n\n")
        f.write(f"**Date:** {time.strftime('%Y-%m-%d')}\n\n")
        f.write("## Results\n\n")
        f.write(f"| Model | Prompt | Valid/Total | Sealed | Time (s) | Error |\n")
        f.write(f"|---|---|---|---|---|---|\n")
        for r in results:
            err = r.error[:50] if r.error else "-"
            f.write(f"| {r.model} | {r.prompt} | {r.valid_tool_calls}/{r.total_tool_calls} | "
                    f"{'Yes' if r.sealed else 'No'} | {r.duration_seconds:.1f} | {err} |\n")
        f.write("\n## Tool Call Breakdowns\n\n")
        for r in results:
            f.write(f"**{r.model}** — \"{r.prompt}\": {r.tool_breakdown}\n\n")

    print(f"\nFindings written to {findings}")


if __name__ == "__main__":
    main()
