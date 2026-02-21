# QA Challenge Response — Principal Architect

**Date:** 2026-02-15
**Context:** Responses to 6 QA challenges raised against the Pixel Art Collectible Platform architecture.
**Disposition:** 5 REFINE, 1 JUSTIFY (with enhancement)

---

## Challenge 1: Polling UX Lag

**Response: REFINE**

QA is correct. The original adaptive polling design (5s idle to 500ms active) introduces an unacceptable cold-start latency of up to 5 seconds between the user clicking "Generate" and the agent picking up the job. The user sees nothing during `PENDING -> WAITING_FOR_AGENT`, which feels broken.

### Refinement: SSE Kick + Adaptive Polling Hybrid

The polling model is retained for agent-to-platform communication (it is the correct pattern for a pull architecture where the platform never connects outbound), but we add a **Server-Sent Events (SSE) channel** on the **frontend-to-platform** path to eliminate perceived lag.

**How it works:**

1. User clicks "Generate" on frontend.
2. Frontend sends `POST /api/generations` to platform. Platform creates job in `PENDING` state, debits credits, returns `job_id` immediately.
3. Frontend opens SSE connection: `GET /api/generations/{job_id}/events`.
4. Platform immediately pushes SSE event `{"state": "WAITING_FOR_AGENT", "message": "Connecting to your local model..."}`.
5. Agent picks up job on next poll. Upon pickup, platform pushes SSE `{"state": "EXECUTING_TOOLS", "step": 0, "message": "Your model is creating art..."}`.
6. As each tool call completes server-side, platform pushes progress: `{"state": "EXECUTING_TOOLS", "step": 37, "total_estimate": 200, "last_tool": "fill_rect"}`.
7. On completion: `{"state": "COMPLETE", "art_id": "...", "preview_url": "..."}`.

**Agent polling is unchanged.** The agent still polls `GET /api/agent/jobs` on the adaptive schedule. The SSE channel is purely frontend-to-platform; the platform never connects outbound to the user's machine.

**Addressing the 5-second gap specifically:**

- The moment the user clicks Generate, the frontend immediately transitions to a generation-in-progress view with an animated status indicator.
- The SSE stream provides real-time state updates so the user always knows exactly what is happening.
- If the agent has not picked up the job within 10 seconds, the SSE stream pushes: `{"state": "WAITING_FOR_AGENT", "warning": "agent_slow", "message": "Waiting for your local agent. Is it running?"}`. This turns a mysterious delay into an actionable diagnostic.
- If the agent has not picked up within 60 seconds, the SSE stream pushes: `{"state": "WAITING_FOR_AGENT", "warning": "agent_timeout_warning", "message": "Your agent hasn't responded. Check that it's running and connected to the internet."}`.

**Agent-side polling refinement:**

The adaptive polling schedule is also tightened:

| Agent State | Poll Interval |
|---|---|
| Idle (no jobs in 5 min) | 3s (was 5s) |
| Recently active (job in last 5 min) | 1s |
| Active (job in progress) | 500ms |
| Freshly started | 1s for first 30s, then idle schedule |

Maximum cold-start delay is now 3 seconds, and in the common case where the user is actively generating, it is 1 second. Combined with the SSE stream providing instant visual feedback, the perceived lag is eliminated.

**SSE connection lifecycle:**

- SSE connection is per-generation, not per-session. Frontend opens it when generation starts and closes on `COMPLETE` or `FAILED`.
- If the SSE connection drops, the frontend falls back to polling `GET /api/generations/{id}` every 2 seconds. The SSE stream is a UX enhancement, not a correctness requirement.
- SSE heartbeat every 15 seconds to keep the connection alive and detect stale connections.

---

## Challenge 2: Zombie State Machine

**Response: REFINE**

QA is correct. The original proposal described saga compensation at a high level but never specified timeout transitions or zombie detection. A job stuck in `EXECUTING_TOOLS` because the user's Ollama crashed would remain there indefinitely.

### Refinement: Heartbeat-Based Zombie Detection with Explicit Timeouts

**1. Agent heartbeat requirement:**

While a job is in `EXECUTING_TOOLS`, the agent must send periodic heartbeats to the platform. These are piggybacked on the result submission endpoint:

```
POST /api/agent/heartbeat
{
  "job_id": "...",
  "agent_version": "1.0.0",
  "ollama_status": "healthy"
}
```

The platform records `last_agent_heartbeat_at` on the job row. If the agent is actively relaying tool calls, each `POST /api/agent/result` also counts as a heartbeat (no separate call needed). The explicit heartbeat endpoint is only needed during long Ollama inference pauses where the model is "thinking" but has not yet returned a tool call.

Required heartbeat interval: **every 30 seconds**.

**2. Timeout table:**

| State | Timeout | Action |
|---|---|---|
| `PENDING` | 5 minutes | Transition to `FAILED`, refund credits |
| `WAITING_FOR_AGENT` | 2 minutes | Transition to `FAILED`, refund credits, push SSE warning |
| `EXECUTING_TOOLS` (no heartbeat) | 90 seconds since last heartbeat | Transition to `STALLED` |
| `STALLED` | 5 minutes (grace period) | Transition to `FAILED`, partial compensation (see Challenge 3) |
| `SEALING` | 60 seconds | Transition to `FAILED`, full refund (platform fault) |

**3. New state: `STALLED`**

The state machine gains a `STALLED` state to distinguish between "definitely dead" and "possibly recoverable":

```
PENDING -> WAITING_FOR_AGENT -> EXECUTING_TOOLS -> SEALING -> COMPLETE
                                      |
                                      v
                                   STALLED -----(grace)----> FAILED
                                      ^                         ^
                                      |                         |
                                (heartbeat timeout)    (any state timeout)
```

If the agent reconnects while the job is in `STALLED`, it can resume:
- Platform checks if the in-memory canvas is still available (see Challenge 4 refinement for Redis-backed canvas).
- If canvas is available, job transitions back to `EXECUTING_TOOLS` and resumes from the last completed tool call.
- If canvas has been evicted, job transitions to `FAILED` with partial compensation.

**4. Zombie reaper (background task):**

A FastAPI background task runs every 30 seconds and queries:

```sql
SELECT job_id, status, last_agent_heartbeat_at, updated_at
FROM generation_jobs
WHERE status IN ('PENDING', 'WAITING_FOR_AGENT', 'EXECUTING_TOOLS', 'STALLED', 'SEALING')
  AND updated_at < NOW() - INTERVAL '90 seconds';
```

For each result, it applies the timeout rules from the table above. This is a simple `asyncio.create_task` in the FastAPI lifespan, not a separate worker process.

**5. User-initiated cancellation:**

Users can cancel at any time via `POST /api/generations/{id}/cancel`. This transitions the job to `FAILED` regardless of current state, applies appropriate compensation (see Challenge 3), and notifies the agent on its next poll that the job is cancelled.

**6. Per-user concurrency limit:**

Only 1 active generation per user at a time. This prevents a user with a flaky agent from accumulating zombie jobs. The platform rejects `POST /api/generations` if the user already has a job in any active state (`PENDING`, `WAITING_FOR_AGENT`, `EXECUTING_TOOLS`, `STALLED`).

---

## Challenge 3: Saga Compensation Underspecified

**Response: REFINE**

QA is correct. The original proposal mentioned saga compensation without defining concrete policies. Here are the specific policies for each failure scenario.

### Compensation Policy Framework

**Core principle:** The platform debits credits optimistically at generation start. If the generation fails for any reason, credits are refunded based on who is at fault and how much work was completed.

**Credit transaction model:**

```sql
CREATE TABLE credit_transactions (
    txn_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(user_id),
    job_id          UUID REFERENCES generation_jobs(job_id),
    amount          INTEGER NOT NULL,  -- positive = credit, negative = debit
    txn_type        VARCHAR(30) NOT NULL
                    CHECK (txn_type IN ('purchase', 'debit', 'refund_full', 'refund_partial', 'compensation')),
    reason          TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Every debit and refund is a separate row. The user's balance is `SUM(amount) FROM credit_transactions WHERE user_id = ?`. This provides a complete audit trail.

### Failure Scenario 1: Agent never picks up job

**Trigger:** Job in `WAITING_FOR_AGENT` exceeds 2-minute timeout.
**Fault:** User (agent not running or misconfigured).
**Compensation:** Full credit refund.
**Rationale:** No platform resources were consumed beyond creating a database row. The user should not be penalized for agent issues.

```
credit_transactions: INSERT (user_id, job_id, amount=+5, txn_type='refund_full', reason='agent_timeout')
```

### Failure Scenario 2: Ollama crashes mid-generation (agent loses internet or Ollama dies)

**Trigger:** Job in `EXECUTING_TOOLS`, agent heartbeat stops, job transitions to `STALLED`, grace period expires (5 minutes), job transitions to `FAILED`.
**Fault:** User (local infrastructure failure).
**Compensation:** **Partial refund** based on percentage of tool calls completed.

Formula:
```
completed_ratio = completed_tool_calls / tier_tool_call_estimate
refund_credits = ceil(original_credits * (1 - completed_ratio))
```

Where `tier_tool_call_estimate` is:
- Small: 30 (conservative estimate, not a hard limit - see Challenge 5)
- Medium: 120
- Large: 300

Examples:
- Large (5 credits), 50 of 300 estimated calls done (17%): refund `ceil(5 * 0.83)` = 5 credits (round up in user's favor).
- Large (5 credits), 250 of 300 estimated calls done (83%): refund `ceil(5 * 0.17)` = 1 credit.
- If ratio > 90% completed: no refund (the art is substantially complete).

**But wait:** If the agent reconnects within the 5-minute `STALLED` grace period and the canvas is still available in Redis, the job resumes with no compensation needed. Compensation only triggers on final `FAILED` transition.

```
credit_transactions: INSERT (user_id, job_id, amount=+4, txn_type='refund_partial',
  reason='agent_disconnect_at_step_50_of_300')
```

### Failure Scenario 3: Platform fault during tool execution

**Trigger:** Platform crashes, database goes down, or tool execution throws an unhandled exception during `EXECUTING_TOOLS` or `SEALING`.
**Fault:** Platform.
**Compensation:** **Full refund + 1 bonus credit** as goodwill.

```
credit_transactions: INSERT (user_id, job_id, amount=+5, txn_type='refund_full', reason='platform_fault')
credit_transactions: INSERT (user_id, job_id, amount=+1, txn_type='compensation', reason='goodwill_platform_fault')
```

**Bonus credit cap:** Maximum 5 goodwill credits per user per 24-hour period. This prevents exploitation if someone discovers a way to trigger platform faults repeatedly.

### Failure Scenario 4: User cancels mid-generation

**Trigger:** User sends `POST /api/generations/{id}/cancel` while job is in `EXECUTING_TOOLS`.
**Fault:** Neither (user choice).
**Compensation:** **Partial refund** using the same formula as Scenario 2, but with a minimum refund of 50% of original credits. The user chose to stop, so we are generous.

```
refund_credits = max(ceil(original_credits * (1 - completed_ratio)), ceil(original_credits * 0.5))
```

### Failure Scenario 5: Model produces invalid/offensive output

**Trigger:** Content moderation flags the completed art, or tool call validation fails repeatedly (model outputs gibberish tool calls 5 times in a row).
**Fault:** Neither (model quality issue).
**Compensation:** **Full refund.** The user did nothing wrong; their model just was not up to the task.

```
credit_transactions: INSERT (user_id, job_id, amount=+5, txn_type='refund_full', reason='content_rejected')
```

### Summary Table

| Scenario | Fault | Compensation | Timeline |
|---|---|---|---|
| Agent never connects | User | Full refund | After 2 min timeout |
| Agent disconnects mid-gen | User | Partial (proportional) | After 5 min grace |
| Agent reconnects during grace | - | None (resumes) | Within 5 min |
| Platform fault | Platform | Full + 1 bonus credit | Immediate |
| User cancels | User choice | Partial (min 50%) | Immediate |
| Model outputs garbage/offensive | Model | Full refund | Immediate |

---

## Challenge 4: BYTEA Canvas is Wrong for Iteration

**Response: REFINE**

QA is absolutely correct. Storing WIP canvas as a BYTEA PNG blob and round-tripping through the database on every tool call is an anti-pattern. A 64x64 RGBA canvas is only 16,384 bytes in raw form, but PNG encode/decode plus a database UPDATE on every tool call would dominate latency and I/O.

### Refinement: Redis for WIP Canvas, PostgreSQL for Final Art Only

**Architecture change:** WIP canvas state lives in Redis during generation. PostgreSQL only stores the final sealed artwork.

**WIP Canvas representation:**

The canvas is stored in Redis as a raw byte buffer, not PNG. For a 64x64 RGBA image:

```
Key:    canvas:{job_id}
Value:  Raw RGBA bytes (width * height * 4 bytes)
TTL:    30 minutes (auto-cleanup for abandoned jobs)
```

Size by tier:
- Small (16x16): 1,024 bytes
- Medium (32x32): 4,096 bytes
- Large (64x64): 16,384 bytes

These are trivially small for Redis. Even 1,000 concurrent Large generations would use only 16 MB of Redis memory.

**Tool execution flow (revised):**

```
1. Platform receives tool call from agent
2. GET canvas:{job_id} from Redis (raw bytes)
3. Wrap in Pillow Image.frombytes("RGBA", (w, h), data)  -- zero-copy, no decode
4. Execute tool (set_pixel, fill_rect, etc.)
5. SET canvas:{job_id} = image.tobytes()  -- zero-copy, no encode
6. Return tool result to agent via next poll
```

**Performance comparison:**

| Operation | BYTEA/PNG (old) | Redis/raw bytes (new) |
|---|---|---|
| Read canvas | DB query + PNG decode (~2ms) | Redis GET + frombytes (~0.1ms) |
| Write canvas | PNG encode + DB UPDATE (~3ms) | tobytes + Redis SET (~0.1ms) |
| Per-tool-call overhead | ~5ms | ~0.2ms |
| 500 tool calls (Large) | ~2.5 seconds cumulative | ~0.1 seconds cumulative |

That is a 25x reduction in cumulative canvas I/O overhead for a Large piece.

**Canvas lifecycle:**

```
1. Job enters EXECUTING_TOOLS:
   - Create blank RGBA canvas in Redis with TTL 30 min
   - Store canvas metadata in generation_jobs row (width, height, tier)

2. Each tool call:
   - Read from Redis, apply tool, write back to Redis
   - Also append tool call to operation_log:{job_id} (Redis list) for replay

3. Job enters SEALING:
   - Read final canvas from Redis
   - Render to PNG via Pillow (single encode)
   - Apply watermark
   - Generate HMAC seal
   - Store final PNG to filesystem, metadata to PostgreSQL
   - DELETE canvas:{job_id} and operation_log:{job_id} from Redis

4. Job FAILED or abandoned:
   - Redis TTL auto-expires after 30 minutes
   - Zombie reaper also explicitly DELETEs on FAILED transition
```

**Operation log for resume:**

Each tool call is also appended to a Redis list `operation_log:{job_id}` as a compact JSON entry:

```json
{"seq": 37, "tool": "fill_rect", "args": {"x": 0, "y": 0, "w": 16, "h": 16, "color": [30, 30, 60, 255]}, "ts": 1708012345}
```

If the agent disconnects and reconnects (STALLED -> EXECUTING_TOOLS resume), the platform does NOT need to replay the log — the canvas state in Redis is already current. The log exists for:
1. Debugging (what did the model do?)
2. Time-lapse replay feature (future product feature)
3. Forensic audit if something goes wrong

**Redis failure mode:**

If Redis is unavailable, the generation fails with a platform fault (full refund + goodwill credit per Challenge 3, Scenario 3). Redis is required infrastructure for the generation pipeline, equivalent to PostgreSQL being required for data storage.

**What stays in PostgreSQL:**

- `generation_jobs` table: job metadata, state machine, timestamps
- `art_pieces` table: final sealed artwork metadata
- `credit_transactions` table: all credit movements
- Final PNG: filesystem at `/var/art/{art_id}.png`

PostgreSQL is the system of record. Redis is ephemeral working memory.

---

## Challenge 5: Tool Call Limits Are Guesses

**Response: REFINE**

QA is correct that the original limits (50/200/500) were not empirically justified. The fundamental concern is valid: without testing, we cannot know if these limits produce good art or waste the user's credits on incomplete art.

### Refinement: Replace Hard Limits with Adaptive Budget + Early PoC Validation

**1. Replace static limits with a token-based budget system:**

Instead of a hard tool call limit, each tier gets a **tool call budget** that represents the *expected* range, with a hard ceiling for abuse prevention:

| Tier | Canvas | Budget (soft target) | Hard Ceiling | Rationale |
|---|---|---|---|---|
| Small | 16x16 (256 px) | 30-80 calls | 150 | 256 pixels; bulk fills + detail work |
| Medium | 32x32 (1024 px) | 100-250 calls | 400 | 1024 pixels; more area to cover |
| Large | 64x64 (4096 px) | 200-600 calls | 1000 | 4096 pixels; complex compositions |

The **soft target** is communicated to the model in the system prompt: "You have approximately 200-600 tool calls to complete this piece. Plan your composition accordingly." This gives the model creative guidance without hard-cutting it.

The **hard ceiling** exists solely to prevent runaway loops (model stuck in an infinite tool-calling cycle). When the ceiling is hit, the platform sends a final "wrap up" prompt to the model asking it to finalize the piece, then seals.

**2. Model-driven termination (preferred):**

The model itself should decide when the art is done. We add a `seal_canvas` tool to every tier's whitelist:

```
seal_canvas(): Signal that the artwork is complete. No more tool calls after this.
```

The system prompt instructs the model: "When you are satisfied with your artwork, call seal_canvas() to finalize it. You do not need to use all available tool calls."

This means:
- A skilled model might complete a Small piece in 15 calls (background fill + a few detail strokes + seal).
- A detailed model might use 500 calls on a Large piece before sealing.
- The hard ceiling is a safety net, not the normal termination condition.

**3. Analytical justification for the ranges:**

Here is the pixel math that informs the budget ranges:

**Small (16x16, 256 pixels):**
- Best case: 1 `fill_rect` for background + 10-20 `set_pixel` for details + `seal_canvas` = ~15-25 calls.
- Worst case (pixel-by-pixel): 256 `set_pixel` calls, but no model would do this with `fill_rect` available.
- Budget of 30-80 is generous. Hard ceiling of 150 is ~60% pixel coverage even with pure set_pixel.

**Medium (32x32, 1024 pixels):**
- Background: 1-4 `fill_rect` calls covers most of the canvas.
- Structures/shapes: 20-50 `draw_line`, `draw_circle`, `fill_rect` calls.
- Detail: 30-100 `set_pixel` calls for fine detail.
- Total: 50-150 calls typical. Budget of 100-250 is generous with room for expressive models.

**Large (64x64, 4096 pixels):**
- Background + large regions: 5-10 `fill_rect` + `gradient_fill` calls.
- Mid-level shapes: 30-80 calls with lines, circles, fills.
- Detail work: 50-200 `set_pixel` calls.
- Effects: 5-20 `dither`, `mirror`, `rotate` calls.
- Total: 100-300 typical. Budget of 200-600 covers ambitious pieces. Hard ceiling of 1000 prevents runaway.

**4. Early PoC validation (mandatory before launch):**

This is the most important part. Add a **Phase 0 task** to the implementation plan:

> **Task 0: Tool-Calling PoC with Ollama**
>
> Before building anything else, validate that Ollama models can produce coherent pixel art via tool-calling.
>
> **Steps:**
> 1. Write a standalone Python script that presents the tool whitelist to a local Ollama model (llama3.1, qwen2.5, deepseek-r1) and asks it to create a 16x16 pixel art piece.
> 2. Execute each tool call against a Pillow canvas locally.
> 3. Record: total tool calls used, time taken, output quality (manual inspection).
> 4. Repeat for 32x32 and 64x64.
> 5. Repeat with at least 3 different models.
> 6. Document findings: actual tool call counts, common patterns, failure modes, quality assessment.
> 7. **Gate decision:** If no model can produce recognizable pixel art via tool-calling, pivot the architecture (e.g., switch to structured JSON grid output instead of iterative tool-calling).
>
> **Output:** A data table of (model, canvas_size, tool_calls_used, time_seconds, quality_1_to_5) that empirically validates or invalidates the budget ranges.

This PoC directly addresses QA's concern. If the PoC shows that 500 calls is not enough for 64x64, we adjust the budgets before writing a single line of platform code. If the PoC shows that models cannot do tool-calling-based pixel art at all, we know before investing in the architecture.

**5. Post-launch telemetry:**

After launch, collect actual tool call usage per tier and adjust budgets based on real data:

```sql
CREATE TABLE generation_telemetry (
    job_id              UUID PRIMARY KEY REFERENCES generation_jobs(job_id),
    tier                VARCHAR(20) NOT NULL,
    model_name          VARCHAR(100),
    tool_calls_used     INTEGER NOT NULL,
    tool_calls_ceiling  INTEGER NOT NULL,
    hit_ceiling         BOOLEAN NOT NULL DEFAULT FALSE,
    seal_initiated_by   VARCHAR(20) NOT NULL CHECK (seal_initiated_by IN ('model', 'ceiling', 'timeout', 'user_cancel')),
    generation_seconds  NUMERIC(10,2),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Monthly review: if >20% of generations hit the ceiling, increase it. If <5% use more than half the budget, consider lowering the ceiling.

---

## Challenge 6: No API Contracts Designed

**Response: REFINE**

QA is right. Listing endpoint paths without contracts is insufficient. Below are concrete request/response schemas for every critical endpoint.

### API Contracts

All endpoints return JSON. All error responses follow a consistent envelope:

```json
{
  "error": {
    "code": "INSUFFICIENT_CREDITS",
    "message": "You need 5 credits for a Large generation but only have 3.",
    "details": {}
  }
}
```

**Standard error codes:**

| HTTP Status | Code | Meaning |
|---|---|---|
| 400 | `VALIDATION_ERROR` | Request body failed Pydantic validation |
| 400 | `INVALID_TIER` | Requested tier does not exist |
| 401 | `UNAUTHORIZED` | Missing or invalid JWT |
| 402 | `INSUFFICIENT_CREDITS` | Not enough credits for the requested action |
| 404 | `NOT_FOUND` | Resource does not exist |
| 409 | `GENERATION_IN_PROGRESS` | User already has an active generation |
| 409 | `DUPLICATE_REQUEST` | Idempotency key collision |
| 429 | `RATE_LIMITED` | Too many requests |
| 500 | `INTERNAL_ERROR` | Platform fault |
| 503 | `SERVICE_UNAVAILABLE` | Upstream dependency down (Redis, etc.) |

---

#### 1. Generation API

**POST /api/generations** — Start a new generation

```
Authorization: Bearer <jwt>
Content-Type: application/json
Idempotency-Key: <uuid>
```

Request:
```json
{
  "tier": "large",
  "style_hint": "fantasy landscape"     // optional, passed to model prompt
}
```

Response (201 Created):
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "PENDING",
  "tier": "large",
  "credits_debited": 5,
  "credits_remaining": 15,
  "canvas_size": {"width": 64, "height": 64},
  "created_at": "2026-02-15T12:00:00Z",
  "events_url": "/api/generations/550e8400-e29b-41d4-a716-446655440000/events"
}
```

Error responses: `401`, `402`, `409 GENERATION_IN_PROGRESS`, `409 DUPLICATE_REQUEST`, `429`

---

**GET /api/generations/{job_id}** — Poll generation status

```
Authorization: Bearer <jwt>
```

Response (200 OK, in-progress):
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "EXECUTING_TOOLS",
  "tier": "large",
  "progress": {
    "tool_calls_completed": 37,
    "tool_calls_budget": 600,
    "last_tool": "fill_rect",
    "elapsed_seconds": 45
  },
  "created_at": "2026-02-15T12:00:00Z"
}
```

Response (200 OK, complete):
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "COMPLETE",
  "tier": "large",
  "art_id": "660e8400-e29b-41d4-a716-446655440001",
  "preview_url": "/art/660e8400-e29b-41d4-a716-446655440001/thumbnail.png",
  "full_url": "/art/660e8400-e29b-41d4-a716-446655440001/full.png",
  "tool_calls_used": 347,
  "generation_seconds": 182.5,
  "created_at": "2026-02-15T12:00:00Z",
  "completed_at": "2026-02-15T12:03:02Z"
}
```

Response (200 OK, failed):
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "FAILED",
  "tier": "large",
  "failure_reason": "agent_timeout",
  "credits_refunded": 5,
  "created_at": "2026-02-15T12:00:00Z",
  "failed_at": "2026-02-15T12:02:00Z"
}
```

---

**GET /api/generations/{job_id}/events** — SSE stream for real-time updates

```
Authorization: Bearer <jwt>
Accept: text/event-stream
```

SSE events:
```
event: state_change
data: {"status": "WAITING_FOR_AGENT", "message": "Connecting to your local model..."}

event: state_change
data: {"status": "EXECUTING_TOOLS", "step": 0, "message": "Your model is creating art..."}

event: progress
data: {"step": 37, "budget": 600, "last_tool": "fill_rect"}

event: progress
data: {"step": 38, "budget": 600, "last_tool": "set_pixel"}

event: warning
data: {"code": "agent_slow", "message": "Waiting for your local agent. Is it running?"}

event: complete
data: {"art_id": "...", "preview_url": "...", "full_url": "...", "tool_calls_used": 347}

event: failed
data: {"reason": "agent_timeout", "credits_refunded": 5}

event: heartbeat
data: {}
```

---

**POST /api/generations/{job_id}/cancel** — Cancel an in-progress generation

```
Authorization: Bearer <jwt>
```

Response (200 OK):
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "FAILED",
  "cancellation": {
    "tool_calls_completed": 120,
    "credits_refunded": 3,
    "refund_policy": "partial_min_50_percent"
  }
}
```

---

#### 2. Agent Gateway API

**GET /api/agent/jobs** — Poll for pending jobs

```
Authorization: Bearer <agent_token>
```

Response (200 OK, job available):
```json
{
  "job": {
    "job_id": "550e8400-e29b-41d4-a716-446655440000",
    "tier": "large",
    "canvas_size": {"width": 64, "height": 64},
    "system_prompt": "You are a pixel art artist. Create a 64x64 pixel art piece...",
    "tools": [
      {
        "name": "set_pixel",
        "description": "Set a single pixel to a color",
        "parameters": {
          "type": "object",
          "properties": {
            "x": {"type": "integer", "minimum": 0, "maximum": 63},
            "y": {"type": "integer", "minimum": 0, "maximum": 63},
            "color": {
              "type": "array",
              "items": {"type": "integer", "minimum": 0, "maximum": 255},
              "minItems": 4,
              "maxItems": 4,
              "description": "RGBA color [r, g, b, a]"
            }
          },
          "required": ["x", "y", "color"]
        }
      },
      {
        "name": "fill_rect",
        "description": "Fill a rectangle with a solid color",
        "parameters": {
          "type": "object",
          "properties": {
            "x": {"type": "integer", "minimum": 0, "maximum": 63},
            "y": {"type": "integer", "minimum": 0, "maximum": 63},
            "width": {"type": "integer", "minimum": 1, "maximum": 64},
            "height": {"type": "integer", "minimum": 1, "maximum": 64},
            "color": {
              "type": "array",
              "items": {"type": "integer", "minimum": 0, "maximum": 255},
              "minItems": 4,
              "maxItems": 4
            }
          },
          "required": ["x", "y", "width", "height", "color"]
        }
      },
      {"name": "seal_canvas", "description": "Signal that the artwork is complete", "parameters": {"type": "object", "properties": {}}}
    ],
    "style_hint": "fantasy landscape",
    "tool_call_budget": 600,
    "tool_call_ceiling": 1000
  }
}
```

Response (200 OK, no jobs):
```json
{
  "job": null
}
```

Response (200 OK, job cancelled by user):
```json
{
  "job": null,
  "cancelled_jobs": ["550e8400-e29b-41d4-a716-446655440000"]
}
```

---

**POST /api/agent/result** — Submit tool call results from Ollama

```
Authorization: Bearer <agent_token>
Content-Type: application/json
```

Request:
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "tool_calls": [
    {
      "id": "call_001",
      "name": "fill_rect",
      "arguments": {
        "x": 0, "y": 0, "width": 64, "height": 64,
        "color": [30, 30, 60, 255]
      }
    },
    {
      "id": "call_002",
      "name": "set_pixel",
      "arguments": {
        "x": 10, "y": 5,
        "color": [255, 255, 200, 255]
      }
    }
  ]
}
```

Response (200 OK):
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "EXECUTING_TOOLS",
  "results": [
    {
      "call_id": "call_001",
      "success": true,
      "result": {"pixels_affected": 4096}
    },
    {
      "call_id": "call_002",
      "success": true,
      "result": {"pixels_affected": 1}
    }
  ],
  "tool_calls_completed": 39,
  "tool_calls_remaining_before_ceiling": 961,
  "canvas_snapshot_base64": null
}
```

Response (200 OK, tool validation failure):
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "EXECUTING_TOOLS",
  "results": [
    {
      "call_id": "call_001",
      "success": false,
      "error": {
        "code": "OUT_OF_BOUNDS",
        "message": "x=70 exceeds canvas width of 64"
      }
    }
  ],
  "tool_calls_completed": 39,
  "tool_calls_remaining_before_ceiling": 961,
  "consecutive_failures": 1,
  "max_consecutive_failures": 5
}
```

The agent must feed these results back to Ollama as tool call responses so the model can self-correct. After 5 consecutive validation failures, the platform terminates the generation with a full refund (model quality issue).

---

**POST /api/agent/heartbeat** — Keep-alive during long model inference

```
Authorization: Bearer <agent_token>
Content-Type: application/json
```

Request:
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "agent_version": "1.0.0",
  "ollama_status": "healthy",
  "model_name": "llama3.1:8b"
}
```

Response (200 OK):
```json
{
  "acknowledged": true,
  "job_status": "EXECUTING_TOOLS",
  "time_since_last_tool_call_seconds": 25
}
```

Response (200 OK, job was cancelled):
```json
{
  "acknowledged": true,
  "job_status": "FAILED",
  "cancellation_reason": "user_cancelled"
}
```

---

#### 3. Credit API

**GET /api/credits** — Get current balance

```
Authorization: Bearer <jwt>
```

Response (200 OK):
```json
{
  "balance": 15,
  "recent_transactions": [
    {
      "txn_id": "...",
      "amount": -5,
      "txn_type": "debit",
      "reason": "Large generation",
      "job_id": "...",
      "created_at": "2026-02-15T12:00:00Z"
    },
    {
      "txn_id": "...",
      "amount": 25,
      "txn_type": "purchase",
      "reason": "$10 credit pack",
      "created_at": "2026-02-15T11:50:00Z"
    }
  ]
}
```

---

**POST /api/credits/purchase** — Buy a credit pack

```
Authorization: Bearer <jwt>
Content-Type: application/json
Idempotency-Key: <uuid>
```

Request:
```json
{
  "pack": "medium"
}
```

Valid packs: `small` ($5 = 10 credits), `medium` ($10 = 25 credits), `large` ($20 = 60 credits).

Response (201 Created):
```json
{
  "stripe_checkout_url": "https://checkout.stripe.com/c/pay/...",
  "session_id": "cs_live_...",
  "pack": "medium",
  "credits": 25,
  "price_cents": 1000
}
```

Credits are added when the Stripe webhook confirms payment, not on this response.

---

#### 4. Agent Authentication

The agent authenticates with a long-lived token (not the user's JWT). The user generates an agent token from the platform UI:

**POST /api/agent/token** — Generate agent authentication token

```
Authorization: Bearer <jwt>
```

Response (201 Created):
```json
{
  "agent_token": "pat_a1b2c3d4e5f6...",
  "expires_at": "2026-08-15T00:00:00Z",
  "scopes": ["jobs:read", "results:write", "heartbeat:write"]
}
```

The agent token is scoped to only the agent gateway endpoints. It cannot access the user's profile, credits, or marketplace. This limits blast radius if the token is compromised.

---

#### Rate Limits for Agent Endpoints

| Endpoint | Limit | Window |
|---|---|---|
| `GET /api/agent/jobs` | 120 req/min | Per agent token |
| `POST /api/agent/result` | 60 req/min | Per agent token |
| `POST /api/agent/heartbeat` | 10 req/min | Per agent token |

These are generous enough for the adaptive polling schedule (max 2 req/sec at 500ms polling = 120/min) while preventing abuse.

---

## Updated Proposal Summary

### Architecture Changes from QA Review

1. **SSE for frontend real-time updates.** The frontend opens an SSE connection per generation for instant state change notifications. Agent polling is unchanged (platform never connects outbound). SSE is a UX enhancement; frontend falls back to HTTP polling if SSE drops.

2. **STALLED state + heartbeat-based zombie detection.** New state `STALLED` between `EXECUTING_TOOLS` and `FAILED`, with a 5-minute grace period for agent reconnection. Agent must heartbeat every 30 seconds during generation. Background reaper task runs every 30 seconds to enforce timeouts. Per-user concurrency limit of 1 active generation prevents zombie accumulation.

3. **Concrete saga compensation policies.** Five defined failure scenarios with specific credit refund formulas. Partial refunds are proportional to work completed. Platform faults get full refund + goodwill credit. User cancellation gets minimum 50% refund. All credit movements recorded as separate rows in `credit_transactions` for audit trail.

4. **Redis for WIP canvas, PostgreSQL for final art.** Canvas stored as raw RGBA bytes in Redis during generation (~0.1ms per tool call vs ~5ms for PNG/BYTEA). Operation log stored in Redis list for debugging and replay. Redis TTL auto-cleans abandoned canvases. Final PNG rendered once at seal time and stored to filesystem.

5. **Adaptive tool call budgets + mandatory PoC.** Soft budget ranges communicated to model in system prompt. Hard ceiling prevents runaway loops. `seal_canvas` tool lets the model self-terminate. Phase 0 PoC task added to validate that Ollama models can actually produce pixel art via tool-calling before building the platform. Post-launch telemetry table for empirical budget adjustment.

6. **Complete API contracts.** Request/response schemas defined for all critical endpoints: generation lifecycle, agent gateway, credit management. Consistent error envelope with typed error codes. Agent authentication via scoped long-lived tokens separate from user JWTs. Rate limits specified per endpoint.

### Revised State Machine

```
PENDING ──(timeout 5m)──────────────────────────────> FAILED (full refund)
   │
   v
WAITING_FOR_AGENT ──(timeout 2m)────────────────────> FAILED (full refund)
   │
   v
EXECUTING_TOOLS ──(heartbeat timeout 90s)──> STALLED
   │                                            │
   │ (seal_canvas or ceiling)        (reconnect)│──> EXECUTING_TOOLS (resume)
   │                                            │
   v                                    (grace 5m)
SEALING ──(timeout 60s)──────────────────────────────> FAILED (full refund + goodwill)
   │                                            │
   v                                            v
COMPLETE                                     FAILED (partial refund)
```

Any state can also transition to FAILED via user cancellation (`POST /api/generations/{id}/cancel`).

### Revised Tool Whitelist with Budgets

| Tier | Canvas | Tools | Budget | Ceiling |
|---|---|---|---|---|
| Small (1 credit) | 16x16 | set_pixel, fill_rect, set_palette, seal_canvas | 30-80 | 150 |
| Medium (3 credits) | 32x32 | + draw_line, draw_circle, flood_fill | 100-250 | 400 |
| Large (5 credits) | 64x64 | + gradient_fill, dither, mirror, rotate | 200-600 | 1000 |

### Revised Implementation Plan (Phase 0 Added)

**Phase 0: Validation**
- Task 0: Tool-calling PoC with Ollama (gate decision before any platform code)

**Phase 1: Foundation** (Tasks 1-5, unchanged except Redis added to infrastructure)
- Task 1: Project setup (add Redis to requirements)
- Task 2: Database schema (add credit_transactions, generation_telemetry tables)
- Task 3: User auth
- Task 4: Rate limiting + security headers
- Task 5: Stripe payment integration

**Phase 2: Core Generation** (Tasks 6-10, revised)
- Task 6: Tier resolver (with budget ranges)
- Task 7: Agent gateway API (with contracts defined above)
- Task 8: Canvas renderer + Redis WIP storage
- Task 9: Tool harness + Command Pattern (with seal_canvas, validation, consecutive failure detection)
- Task 10: Generation orchestrator + state machine (with STALLED state, zombie reaper, SSE events)

**Phase 3-5:** Unchanged from original proposal.

### New Infrastructure Requirements

| Component | Purpose | Failure Mode |
|---|---|---|
| PostgreSQL | System of record | Platform down |
| Redis | WIP canvas + operation logs | Generation fails (refund) |
| Filesystem | Final PNG storage | Art display fails (retryable) |

### Key Invariants

1. The platform NEVER connects outbound to the user's machine. All communication is agent-initiated polling.
2. Tool execution is ALWAYS server-side. The agent is a relay, never an executor.
3. Every credit debit has a corresponding refund path for every failure mode.
4. WIP state (Redis) is ephemeral. Only sealed art reaches PostgreSQL and filesystem.
5. The PoC (Task 0) must pass before any platform code is written.
