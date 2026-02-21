# PoC: Ollama Tool-Calling for Pixel Art

**Date:** 2026-02-15
**Result:** PASS — concept validated

## Summary

Tested 4 Ollama models for generating 16x16 pixel art via iterative tool-calling (set_pixel, fill_rect, draw_line, seal_canvas).

## Recommended Model: qwen2.5:7b

- Best quality output (recognizable subjects with structured composition)
- 66% reliability (2/3 prompts succeeded, 1 timed out)
- ~20-27s per generation
- Properly uses seal_canvas

## All Results

| Model | Reliability | Quality | Notes |
|---|---|---|---|
| qwen2.5:7b | 66% | Best | Recognizable tree, structured shapes |
| qwen3-nothink | 100% | Low | Seals too quickly (6-7 calls), just rectangles |
| llama3.1:8b | 0% sealed | Medium | Uses tools but never calls seal_canvas |
| qwen3:8b | 33% | Low | Thinking overhead causes timeouts |

## Production Recommendations

1. **Primary model:** qwen2.5:7b
2. **Minimum tool calls before seal:** Enforce in harness (e.g., 15+ for small tier)
3. **Color reference table in system prompt:** Dramatically improves color accuracy
4. **Retry logic:** If generation times out, retry once with same prompt
5. **Timeout:** 180s per generation (120s too aggressive for some prompts)
6. **System prompt:** Explicit "ONLY use tool calls, do NOT respond with text"

## Output Images

See `output/` directory for generated PNGs (256x256 scaled + 16x16 raw).
