# The Dialog Bridge

The bridge is one FastAPI process. It is the only component that decides task
state, and the only one whose database must survive for recovery to work.

## Components

| Component | File | Responsibility |
| --- | --- | --- |
| Fault proxy | `proxy.py` | Middleware applying fault rules per link before any handler runs ([FAULT_INJECTION.md](FAULT_INJECTION.md)) |
| Codecs | `codecs/mcp_style.py`, `codecs/a2a_style.py` | Native format ↔ shared envelope ([PROTOCOLS.md](PROTOCOLS.md)) |
| Routes | `main.py` | Entry points; schema version check; error mapping |
| Models | `models.py` | Pydantic models for the schema ([SCHEMA.md](SCHEMA.md)) |
| Inbox | `inbox.py` | Stores the reply to every state-changing `request_id` |
| State machine | `state_machine.py` | Allowed transitions + guards ([STATE_MACHINE.md](STATE_MACHINE.md)) |
| Leases | `leases.py` | Claim, resume, heartbeat, fence checks |
| Events | `events.py` | Append numbered events; fan out to WebSocket clients |
| Registry | `registry.py` | Known agents, protocol, last heartbeat |
| Metrics | `metrics.py` | Counters and timings computed from events |
| Jobs | `jobs.py` | Lease watchdog, dispatcher, startup recovery |
| Database | `db.py` | Connection, WAL, schema creation, `transaction()` helper |

## Entry points

Agents talk to the bridge **only** through their protocol entrance:

| Route | Who | Format |
| --- | --- | --- |
| `POST /mcp` | Agent A | MCP-style JSON-RPC ([PROTOCOLS.md](PROTOCOLS.md)) |
| `POST /a2a` | Agent B, Agent B2 | A2A-style messages ([PROTOCOLS.md](PROTOCOLS.md)) |

Read-only and control routes for the dashboard, tests and supervisor (plain JSON):

| Route | Purpose |
| --- | --- |
| `GET /` | Serves the dashboard |
| `GET /health` | `{"ok": true, "boot_id": ..., "last_seq": ...}` |
| `GET /dialogs`, `GET /dialogs/{id}` | Dialogs with their tasks |
| `GET /tasks/{id}` | One task, shared-schema form |
| `GET /tasks/{id}/views` | The same task in shared, MCP-style and A2A-style form (inspector) |
| `GET /events?since=N&task_id=&type=` | Event log, filterable |
| `GET /agents` | Registry |
| `GET /metrics` | Counters ([below](#metrics)) |
| `POST /faults`, `GET /faults`, `DELETE /faults` | Fault rules |
| `POST /annotate` | Adds a `test_step` event (watch-mode captions) |
| `WS /ws` | Live event stream |
| `POST /admin/export` | Writes events, tasks and metrics to a folder (test evidence) |

FastAPI's automatic page at `/docs` lists all of these and lets you call them by hand.

## Database

One file: `data/<mode>/bridge.db`. On open:

```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous = FULL;     -- a committed transaction survives a hard kill
PRAGMA foreign_keys = ON;
```

### Tables

```sql
CREATE TABLE dialogs (
  dialog_id   TEXT PRIMARY KEY,
  msg_id      TEXT NOT NULL UNIQUE,      -- one dialog per user message
  text        TEXT NOT NULL,
  created_by  TEXT NOT NULL,
  created_at  TEXT NOT NULL
);

CREATE TABLE tasks (
  task_id            TEXT PRIMARY KEY,
  dialog_id          TEXT NOT NULL REFERENCES dialogs(dialog_id),
  idx                INTEGER NOT NULL,
  intent             TEXT NOT NULL,
  action             TEXT,               -- pay | send | reserve | NULL
  params             TEXT NOT NULL,      -- JSON
  steps              TEXT NOT NULL,      -- JSON list
  state              TEXT NOT NULL,
  owner              TEXT,
  lease_until        REAL,               -- bridge clock, epoch seconds
  fence              INTEGER NOT NULL DEFAULT 0,
  attempt            INTEGER NOT NULL DEFAULT 0,
  max_attempts       INTEGER NOT NULL DEFAULT 3,
  depends_on         TEXT NOT NULL DEFAULT '[]',
  needs_confirmation INTEGER NOT NULL DEFAULT 0,
  confirmed          INTEGER NOT NULL DEFAULT 0,
  effect_key         TEXT,
  effect_status      TEXT NOT NULL DEFAULT 'none',
  receipt_id         TEXT,
  effect_replayed    INTEGER NOT NULL DEFAULT 0,
  result             TEXT,
  reason             TEXT,
  created_at         TEXT NOT NULL,
  updated_at         TEXT NOT NULL,
  UNIQUE (dialog_id, idx)
);

CREATE TABLE checkpoints (
  task_id     TEXT NOT NULL REFERENCES tasks(task_id),
  step_index  INTEGER NOT NULL,
  step        TEXT NOT NULL,
  output      TEXT NOT NULL,             -- JSON
  fence       INTEGER NOT NULL,
  created_at  TEXT NOT NULL,
  PRIMARY KEY (task_id, step_index)
);

CREATE TABLE inbox (
  request_id  TEXT PRIMARY KEY,
  body_hash   TEXT NOT NULL,             -- detects request_id reuse with a different body
  op          TEXT NOT NULL,
  status_code INTEGER NOT NULL,
  reply       TEXT NOT NULL,             -- JSON, returned unchanged on repeat
  created_at  TEXT NOT NULL
);

CREATE TABLE events (
  seq        INTEGER PRIMARY KEY AUTOINCREMENT,
  ts         TEXT NOT NULL,
  dialog_id  TEXT,
  task_id    TEXT,
  type       TEXT NOT NULL,
  actor      TEXT NOT NULL,
  data       TEXT NOT NULL              -- JSON
);

CREATE TABLE agents (
  agent_id       TEXT PRIMARY KEY,
  protocol       TEXT NOT NULL,          -- mcp-style | a2a-style
  base_url       TEXT NOT NULL,
  last_heartbeat REAL,
  status         TEXT NOT NULL           -- online | unresponsive | unknown
);
```

### The transaction rule

Every state-changing request runs inside one transaction:

```python
with db.transaction():                 # BEGIN IMMEDIATE ... COMMIT
    stored = inbox.lookup(req.request_id)
    if stored:                         # repeat: return the old reply
        return stored
    reply = handle(req)                # state machine + leases + writes
    events.append(...)                 # inside the same transaction
    inbox.store(req.request_id, reply) # inside the same transaction
return reply
```

`BEGIN IMMEDIATE` takes the write lock at the start, so two concurrent claims
cannot both read "free" and both write "mine". SQLite allows one writer at a
time; the bridge additionally serialises writes through one `threading.Lock`
around the connection, which keeps the code simple and is fast enough for this
workload.

Events are pushed to WebSocket clients **after** commit, so the dashboard never
shows something that was rolled back.

## Leases and fences

```sql
-- task.claim / task.claim_next: one statement, so it is atomic
UPDATE tasks
SET owner = :agent,
    lease_until = :now + :lease,
    fence = fence + 1,
    attempt = attempt + 1,
    state = 'IN_PROGRESS',
    updated_at = :ts
WHERE task_id = :task_id
  AND state IN ('DISPATCHED', 'SUSPENDED')
  AND attempt < max_attempts
  AND (lease_until IS NULL OR lease_until < :now);
-- rowcount 1 -> claimed; rowcount 0 -> 409 already_claimed (or invalid_transition)
```

`task.resume` uses the same statement with one extra case: the sender may
re-claim a task it **already owns** even if the lease has not expired yet (it
restarted quickly), matching `state IN ('IN_PROGRESS','EFFECT_PENDING') AND owner = :agent`.
The fence still increases, so anything the previous incarnation of that agent
sends late is rejected.

- Every write by a worker carries its `fence`. If `fence < tasks.fence` →
  `409 stale_fence` and a `stale_fence_blocked` event.
- `agent.heartbeat` extends `lease_until` for every task the sender owns with
  the current fence.
- Lease times use the bridge's clock only.

## Background jobs

**Lease watchdog** (every `WATCHDOG_TICK`):

1. Tasks in IN_PROGRESS or EFFECT_PENDING with `lease_until < now` → SUSPENDED
   (`lease_expired`). In the same transaction, if `attempt >= max_attempts`,
   the task continues SUSPENDED → FAILED (`max_attempts_reached`) and its
   dependents are cancelled. The watchdog never skips SUSPENDED, so every
   move it makes is in the transition table.
2. Agents with no heartbeat for `2 × HEARTBEAT_SECONDS` → `unresponsive`.

**Dispatcher** (every `DISPATCH_TICK`):

1. CREATED tasks: if any dependency is FAILED or CANCELLED → CANCELLED
   (`dependency_failed`). If all are COMPLETED → AWAITING_CONFIRMATION or
   DISPATCHED.
2. AWAITING_CONFIRMATION tasks with `confirmed = 1` and deps done → DISPATCHED.

**Startup recovery** (once, before serving requests):

1. Open the database and run an integrity check.
2. For every IN_PROGRESS or EFFECT_PENDING task, set
   `lease_until = now + STARTUP_GRACE`. The agents were probably fine; it was
   the bridge that went away. They get one grace period to heartbeat.
3. Set every agent's status to `unknown` until it heartbeats.
4. Log `bridge_started` with counts of recovered dialogs and tasks, and the
   first `seq` after restart (proving the sequence continued).
5. Start the jobs, then start serving.

## Metrics

`GET /metrics` returns values computed from the event log and tables:

| Metric | Source |
| --- | --- |
| `duplicate_requests` | `duplicate_request` events (inbox hits) |
| `duplicate_effects_rejected` | `duplicate_rejected` events |
| `stale_writes_blocked` | `stale_fence_blocked` events |
| `invalid_transitions_rejected` | `invalid_transition_rejected` events |
| `schema_rejections` | `schema_rejected` events |
| `lease_expiries` | `lease_expired` events |
| `recoveries` | `resumed` events |
| `reclaims` | `reclaimed` events (claimed by a different agent than before) |
| `tasks_completed` / `failed` / `cancelled` | task states |
| `avg_task_ms` | `claimed` → `completed`, tasks with no suspension |
| `avg_recovery_ms` | `lease_expired` → next `resumed` or `reclaimed` |
| `faults_applied` | `fault_applied` events |

## Configuration

See [CONFIG.md](CONFIG.md). The bridge reads `MODE`, `PORT`, `DATA_DIR`,
`LEASE_SECONDS`, `HEARTBEAT_SECONDS`, `WATCHDOG_TICK`, `DISPATCH_TICK`,
`STARTUP_GRACE`, `MAX_ATTEMPTS`, `DISABLE_DEDUP`, `CORS_ORIGINS`.
