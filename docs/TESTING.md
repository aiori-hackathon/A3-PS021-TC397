# Testing

The tests act like a strict judge: start the system, use it the way a user
would, break it on purpose, then check the facts. They only use public HTTP
endpoints, never internal functions (except the pure unit tests for codecs,
router and state machine). If a test passes, the real system works.

## Test catalogue

| ID | Group | Name | One-line purpose |
| --- | --- | --- | --- |
| S0 | Baseline | Happy path | Everything works with no faults |
| **S1** | **Official** | Lost reply, sender retries | Repeat request → stored reply, no duplicates |
| **S2** | **Official** | Two workers race for one task | Exactly one claim wins |
| **S3** | **Official** | Worker crashes before the side effect | Resume from checkpoint, same task ID |
| **S4** | **Official** | Worker crashes after the side effect, before reporting | Ledger replays the receipt; no second payment |
| **S5** | **Official** | Bridge hard-killed during a network cut | State reloads from SQLite; agents catch up |
| X1 | Extra | Uncertain side-effect outcome | Lost reply from actions → retry same key → 1 effect |
| X2 | Extra | Slow, not dead, worker | Reclaim with higher fence; old owner's writes blocked |
| X3 | Extra | Orchestrator crashes after creating tasks | Still exactly N tasks |
| X4 | Extra | Retry limit | Always-crashing task → FAILED after 3 attempts; dependents cancelled |
| X5 | Extra | Cancel before side effect | CANCELLED, ledger empty |
| X6 | Extra | Dependency fails | Second task never starts |
| X7 | Extra | Duplicate dispatch to worker | Inbox absorbs the duplicate |
| I1 | Interop | MCP-style round trip | Codec is lossless |
| I2 | Interop | A2A-style round trip | Codec is lossless, including SUSPENDED |
| I3 | Interop | Cross-adapter correlation | One dialog/task ID across both formats |
| R1 | Rules | Invalid transition | 409, nothing changes |
| R2 | Rules | Unknown schema version | 400, nothing created |
| R3 | Rules | Stale fence | 409, nothing changes |
| R4 | Rules | Reused request ID, different body | 422 |
| U* | Unit | Router table, state machine table, guards | Fast checks, no servers |
| M1 | Mutation | Protection off | With `DISABLE_DEDUP=1`, S1 and S4 **must fail** |
| C1 | Chaos | Random faults, 50 tasks | All terminal; ≤ 1 effect per key |

S1–S5 are the five disconnect/retry scenarios required by PS-021. Everything
else is reported separately as additional tests.

## The four phases of every scenario

1. **Reset.** `POST /reset` on the supervisor: stop everything, wipe the
   world's data (including SQLite `-wal`/`-shm` files), clear fault rules,
   start everything, wait for health. Every test starts from an identical
   empty world.
2. **Set the trap.** A crash point (supervisor restart with `CRASH_AT`), a
   fault rule (`POST /faults`), or a timing trap (concurrent requests).
3. **Act and wait.** Send the task to Agent A's `/message` with a fixed
   `msg_id`, then `wait_until` the expected end condition.
4. **Check.** Identity, outcome, side-effect counts, and event order.

## Harness layout

```
tests/
├── conftest.py          # world fixture, mode selection, evidence on teardown
├── helpers/
│   ├── clients.py       # SupervisorClient, BridgeClient, AgentAClient, ActionsClient
│   ├── wait.py          # wait_until
│   └── assertions.py    # assert_events_in_order, assert_single_effect, ...
├── unit/
│   ├── test_router.py
│   ├── test_state_machine.py
│   └── test_codecs.py            # I1, I2
├── test_bridge_api.py           # bridge API through /mcp, /a2a, /ws (bare bridge)
├── test_jobs.py                 # watchdog, agent health, startup recovery (bare bridge)
├── test_actions.py              # actions ledger and fences (bare service)
├── test_supervisor.py           # control API (world)
├── test_agents.py               # adapter behaviour the scenarios rely on (world)
├── test_s0_happy_path.py
├── test_s1_lost_reply.py
├── test_s2_race.py
├── test_s3_crash_before_effect.py
├── test_s4_crash_after_effect.py
├── test_s5_bridge_restart.py
├── test_extras.py                # X1–X7
├── test_interop.py               # I3
├── test_rules.py                 # R1–R4
├── test_mutation.py              # M1 (run separately)
└── test_chaos.py                 # C1 (marked slow)
```

`pytest.ini`:

```ini
[pytest]
timeout = 20
markers =
    official: the five PS-021 scenarios
    extra: additional scenarios
    slow: chaos run
    mutation: protection-off check
addopts = -m "not slow and not mutation"
```

### Fixtures

- `world`: resets the supervised world before the test (`POST /reset`) and hands
  out clients for the supervisor, bridge, Agent A, Agent B, B2 and actions.
  Extra environment for one test goes in a marker, for example
  `@pytest.mark.world_env(AUTO_CONFIRM="0")`; watch mode adds
  `AUTO_CONFIRM=1` because the demo world defaults to 0. If no supervisor is
  running for the world, the session starts one with `--no-start`.
- `bridge`, `fresh_bridge`, `actions_service`: one bare process on a free port
  with its own data folder. Used by tests that must not have agents running
  (for example, R1–R4 claim tasks by hand, which a live Agent B would race).

## Helpers

```python
def wait_until(check, timeout=10, every=0.05):
    end = time.monotonic() + timeout * TIMEOUT_FACTOR   # 1 fast, 5 watch
    last = None
    while time.monotonic() < end:
        last = check()
        if last:
            return last
        time.sleep(every)
    raise TimeoutError(f"gave up after {timeout}s, last value: {last!r}")

def assert_events_in_order(events, expected_types):
    """expected_types must appear in this order; other events may sit between."""
    it = iter(e["type"] for e in events)
    missing = [t for t in expected_types if not any(x == t for x in it)]
    assert not missing, f"missing or out of order: {missing}"
```

**Proving something did not happen:** wait for a positive end point first
(the task is COMPLETED, so nothing more can happen), then check the count.
Never "sleep and check it is still 1".

## Timeouts (three levels)

1. Every HTTP call: `httpx.Client(timeout=HTTP_TIMEOUT)`.
2. Every wait: `wait_until(..., timeout=…)`.
3. Every test: `pytest-timeout` (`timeout = 20`, ×5 in watch mode).

Results distinguish **FAIL** (a check was false) from **TIMEOUT** (the system
got stuck).

## Scenario specifications

### S0 — Happy path
- **Act:** "Pay ₹40 to the bakery and email the receipt to Rahul" (`msg_id=s0`).
- **Wait:** dialog has 2 tasks, both COMPLETED.
- **Check:** ledger: 1 `pay`, 1 `send`; message task's `claimed` event comes
  after payment task's `completed`; each task's events include
  `claimed → checkpoint… → completed`; Agent A's chat has the summary.
- **Record:** `avg_task_ms` baseline.

### S1 — Lost reply, sender retries
- **Trap:** `{link: agent_a, action: drop_reply, match: {op: dialog.create}}`.
- **Act:** "pay ₹40 to the bakery" (`msg_id=s1`).
- **Check:** exactly 1 dialog for `msg_id=s1`; exactly 1 task; a
  `duplicate_request` event for `s1:create`; ledger count 1; task COMPLETED.

### S2 — Two workers race
- **Trap:** start `agent_b2`; pause both workers' polling (`POST /pause` on
  each); create a dispatched task; call `POST /claim` on both workers at once with
  `asyncio.gather`.
- **Check:** exactly one `ok`, the other `409 already_claimed`; fence = 1;
  resume workers; task COMPLETED; ledger count 1.

### S3 — Crash before the side effect
- **Trap:** restart `agent_b` with `CRASH_AT=before_pay`.
- **Act:** "pay ₹40 to the bakery" (`msg_id=s3`).
- **Wait:** supervisor reports `agent_b` crashed; then `POST /restart/agent_b`
  (no crash point).
- **Check:** same `task_id` before and after; fence 2; attempt 2; ledger 1;
  events in order `claimed, checkpoint, (lease_expired)?, resumed, effect_intent,
  effect_committed, completed`; checkpoints for steps 0–1 were **not** redone
  (no second checkpoint row for them).

### S4 — Crash after the side effect, before reporting
- **Trap:** restart `agent_b` with `CRASH_AT=after_pay_call`.
- **Act / wait / recover:** as S3.
- **Check:** ledger count for `tsk:pay` is **1**; the receipt ID before and
  after is identical; a `duplicate_rejected` event exists (actions returned
  `replayed: true`); task COMPLETED with same `task_id`.

### S5 — Bridge hard-killed during a network cut
- **Trap:** start "pay ₹40 to the bakery" with `STEP_DELAY` raised for this
  test; once the task is IN_PROGRESS, apply `{link: agent_a, action: cut}`.
- **Act:** `POST /kill/bridge` (hard kill) **mid-step**; record `last_seq`;
  `POST /start/bridge`; re-apply the cut for 2 s, then delete it.
- **Check:** after restart, `bridge_started` event shows recovered tasks; first
  new `seq` is `last_seq + 1`; the task keeps its `task_id`, finishes COMPLETED;
  ledger 1; Agent A's chat eventually shows completion (it caught up with
  `events.since`); dashboard reconnect is covered manually in the demo checklist.

### X1 — Uncertain side-effect outcome
- **Trap:** `{link: actions, action: drop_reply, match: {action: pay}}`.
- **Check:** ledger 1; a `duplicate_rejected` event; worker did not crash.

### X2 — Slow, not dead
- **Trap:** start `agent_b2`; `{link: agent_b, action: delay, delay_ms: 3×lease, count: -1}`
  applied after B claims.
- **Check:** `lease_expired`, `reclaimed` by `agent_b2` with fence 2;
  at least one `stale_fence_blocked` event from `agent_b`; ledger 1.

### X3 — Orchestrator crash after creating tasks
- **Trap:** restart `agent_a` with `CRASH_AT=after_create`.
- **Act:** two-intent message (`msg_id=x3`); after crash, restart A; A retries the create.
- **Check:** exactly 2 tasks for the dialog; `duplicate_request` for `x3:create`.

### X4 — Retry limit
- **Trap:** `agent_b` with `CRASH_AT=before_pay`, `CRASH_MODE=always`, `AUTO_RESTART=1`.
- **Act:** two-intent message.
- **Check:** payment task FAILED with reason `max_attempts`, attempt = 3; message
  task CANCELLED (`dependency_failed`); ledger 0; Agent A told the user.

### X5 — Cancel before the side effect
- **Setup:** `AUTO_CONFIRM=0`. **Act:** payment message, then `POST /cancel`.
- **Check:** CANCELLED; ledger 0; cancelling a task with `effect.status = committed` is rejected (409).

### X6 — Dependency fails
- Covered inside X4; separate assertion that the dependent task has **no**
  `claimed` event ever.

### X7 — Duplicate dispatch
- **Trap:** `{link: agent_b, action: duplicate, match: {op: task.checkpoint}}`.
- **Check:** one checkpoint row per step; a `duplicate_request` event; ledger 1.

### I1, I2 — Codec round trips (unit, no servers)
- For every op and every task state: `decode(encode(x)) == x`.
- I2 explicitly checks a SUSPENDED task: `status.state == "working"` and
  `metadata.bridge_state == "SUSPENDED"`, and decoding restores SUSPENDED.

### I3 — Cross-adapter correlation
- Agent A creates via `/mcp`; read `GET /tasks/{id}/views`.
- **Check:** MCP-style view and A2A-style view carry the same `dialog_id`
  (`contextId`) and `task_id` (`id`); states correspond per the mapping table;
  A's `structuredContent` and B's `metadata.bridge_state` agree at every
  checkpoint of a full run.

### R1–R4 — Rules
| ID | Attempt | Expect | Also check |
| --- | --- | --- | --- |
| R1 | Move a COMPLETED task to IN_PROGRESS (claim it) | 409 `invalid_transition` | state still COMPLETED; event logged |
| R2 | Envelope with `schema: "dialog-state/9.9"` | 400 `schema_unsupported` | no dialog created |
| R3 | Checkpoint with fence 1 after a claim made fence 2 | 409 `stale_fence` | no checkpoint row added |
| R4 | Same `request_id`, different payload | 422 `request_id_conflict` | original stored reply unchanged |

### M1 — Protection off (mutation check)
- Run S1 and S4 with `DISABLE_DEDUP=1`:
  `DISABLE_DEDUP=1 pytest -m mutation`.
- **Expected:** both scenarios observe duplicates (S1: 2 dialogs or 2
  payments; S4: ledger count 2). The mutation test **passes when duplicates
  appear**, proving S1 and S4 would catch a broken dedup.
- Report as: "Protection off: 2 payments. Protection on: 1 payment."

### C1 — Chaos run
- 50 random messages from a fixed list; seed `CHAOS_SEED`; random fault rules
  on all links; random crash points; `AUTO_RESTART=1`; `AUTO_CONFIRM=1`.
- **Wait:** every task terminal (timeout 300 s).
- **Check:** for every `effect_key`, ledger count ≤ 1; every COMPLETED
  side-effect task has exactly 1 ledger row; no task stuck non-terminal.
- **Report:** tasks, faults applied, crashes, duplicates rejected, duplicate
  effects (must be 0). Run with `pytest -m slow`.

## Modes

| | Fast | Watch |
| --- | --- | --- |
| Command | `TEST_MODE=fast pytest` | `TEST_MODE=watch pytest -k s4` |
| World | Test world, ports 9000–9004 | Demo world, ports 8000–8004 |
| Timings | Test column of CONFIG | Demo column of CONFIG |
| Timeout factor | 1 | 5 |
| Reset | Full wipe of `data/test` | Full wipe of `data/demo` (demo data is disposable) |
| Captions | Off | Each phase posts a `test_step` event via `/annotate` |

The test code is identical in both modes. Captions are a no-op in fast mode:

```python
world.caption("Setting trap: Agent B will crash right after paying")
```

## Evidence

The `world` fixture's teardown (runs even when the test fails) calls the
bridge's `/admin/export` and saves:

```
results/<run_id>/<test_id>/
├── events.json
├── tasks.json
├── metrics.json
├── ledger.json
├── faults.json        # rules that were applied
└── summary.json       # status, duration, key numbers, git commit, seed, python version
```

`python scripts/build_results.py results/<run_id>` writes the tables into
[RESULTS.md](RESULTS.md).

## Flakiness check

Before demo day:

```bash
TEST_MODE=fast pytest -m "official or extra" --count=20 -x
```

Any failure in 20 repeats is a timing bug. Fix it; do not raise timeouts to hide it.

## CI

`.github/workflows/tests.yml` runs on every push:

```yaml
name: tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -r requirements.txt
      - run: TEST_MODE=fast pytest
      - uses: actions/upload-artifact@v4
        if: always()
        with: { name: results, path: results/ }
```

Whether Actions run depends on the repository owner's settings; check with the
organisers since the repository belongs to their account.

## Libraries

`pytest`, `pytest-timeout`, `pytest-repeat`, `pytest-asyncio`, `httpx`.
