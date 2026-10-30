# Instructions for AI coding assistants

This file tells an AI coding assistant (for example Claude Code) how to work
in this repository. Humans should read the README first.

## What this project is

A Python prototype for AIORI-3 PS-021: a Dialog Bridge between two scripted
mock agent adapters (MCP-style and A2A-style) with an experimental
dialog-state schema, an enforced task state machine, request deduplication,
leases with fencing tokens, an idempotent side-effect ledger, durable recovery
in SQLite, fault injection, and a test suite with five disconnect/retry
scenarios plus extras.

## Read before writing code

1. `docs/SCHEMA.md`, `docs/IDENTIFIERS.md`, `docs/STATE_MACHINE.md` — the contract
2. `docs/BRIDGE.md`, `docs/DEDUP_AND_RECOVERY.md` — the core behaviour
3. The doc for the component you are about to touch
4. `docs/BUILD_PLAN.md` — the current phase and its "done when"

If code and docs disagree, stop and ask which is right. Do not silently change
either.

## Stack (do not add to it without asking)

Python 3.11+, FastAPI, Uvicorn, Pydantic v2, httpx, sqlite3 (standard
library), pytest, pytest-timeout, pytest-repeat, pytest-asyncio. Dashboard:
plain HTML/CSS/JS with **no CDN** and no build step.

## Invariants you must never break

- Only the bridge writes task state, and every state-changing request runs in
  **one** `BEGIN IMMEDIATE` transaction that writes the state change, the
  inbox reply and the event together.
- Every transition goes through `state_machine.py`. No direct `UPDATE … SET state`
  anywhere else.
- Keys are derived from durable IDs exactly as in `docs/IDENTIFIERS.md`. Never
  use `uuid4()` for a `request_id` or `effect_key`.
- The effect key is `{task_id}:{action}`. Never add the fence or attempt to it.
- Every worker write carries the fence; stale fences are rejected.
- Leases use the bridge's clock only.
- Events are pushed to WebSocket clients only after commit.
- Agents keep no state they need after a crash; they read it back from the bridge.
- Only Agent B performs side effects, only via the actions service.
- Every message carries `schema: "dialog-state/0.1-experimental"`.

## Working style

- Test first: write or update the test in `tests/` before the feature.
- Use `wait_until` in tests; never fixed sleeps.
- Async code uses `asyncio.sleep`, never `time.sleep`.
- Keep router, codecs and state-machine guards as pure functions.
- Run `TEST_MODE=fast pytest` before declaring a task done; report the result honestly.
- Keep changes small and focused; one component per change where possible.

## Commands

```bash
python supervisor.py --mode demo        # run everything; dashboard at http://localhost:8000
TEST_MODE=fast pytest                   # main suite
TEST_MODE=fast pytest -m "official"     # S1-S5 only
DISABLE_DEDUP=1 pytest -m mutation      # M1
pytest -m slow                          # C1 chaos
python scripts/build_results.py results/<run_id>
```

## Wording rules for docs, comments and UI text

- Say "experimental schema", "MCP-style", "A2A-style", "mock adapter",
  "scripted", "rule-based intent router".
- Never say "IETF standard", "implements MCP/A2A", "exactly-once", or "AI agent"
  for the scripted agents.
