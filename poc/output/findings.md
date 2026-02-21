# PoC Findings: Ollama Tool-Calling for Pixel Art

**Date:** 2026-02-16

## Results

| Model | Prompt | Valid/Total | Sealed | Time (s) | Error |
|---|---|---|---|---|---|
| llama3.1:8b | a red mushroom | 0/0 | No | 16.1 | - |
| llama3.1:8b | a blue sword | 4/4 | No | 8.4 | - |
| llama3.1:8b | a green tree | 4/4 | No | 15.6 | - |
| qwen2.5:7b | a red mushroom | 10/10 | Yes | 27.0 | - |
| qwen2.5:7b | a blue sword | 0/0 | No | 120.1 | timed out |
| qwen2.5:7b | a green tree | 9/9 | Yes | 21.0 | - |
| qwen3:8b | a red mushroom | 0/0 | No | 120.1 | timed out |
| qwen3:8b | a blue sword | 0/0 | No | 120.1 | timed out |
| qwen3:8b | a green tree | 3/3 | Yes | 72.6 | - |

## Tool Call Breakdowns

**llama3.1:8b** — "a red mushroom": {}

**llama3.1:8b** — "a blue sword": {'fill_rect': 1, 'set_pixel': 2, 'draw_line': 1}

**llama3.1:8b** — "a green tree": {'fill_rect': 2, 'set_pixel': 2}

**qwen2.5:7b** — "a red mushroom": {'fill_rect': 2, 'draw_line': 7, 'seal_canvas': 1}

**qwen2.5:7b** — "a blue sword": {}

**qwen2.5:7b** — "a green tree": {'fill_rect': 1, 'draw_line': 7, 'seal_canvas': 1}

**qwen3:8b** — "a red mushroom": {}

**qwen3:8b** — "a blue sword": {}

**qwen3:8b** — "a green tree": {'fill_rect': 2, 'seal_canvas': 1}

