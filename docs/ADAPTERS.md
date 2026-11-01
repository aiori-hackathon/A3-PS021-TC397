# Mock Agent Adapters

Two scripted agents, one per protocol style. They are **intentionally simple
and deterministic**: they are the test subjects, and the bridge is what is
being tested. Same input → same steps → repeatable tests.

**Rule of thumb:** Agent A thinks and delegates. Agent B does the work.
A never performs a side effect; B never talks to the user.

## Roles

| | Agent A: orchestrator | Agent B: worker |
| --- | --- | --- |
| Protocol | MCP-style (`POST /mcp`) | A2A-style (`POST /a2a`) |
| Port | 8001 (test 9001) | 8002 (test 9002); B2 on 8004 / 9004 |
| Talks to | User, bridge | Bridge, actions service |
| Intent router | Yes ([INTENT_ROUTING.md](INTENT_ROUTING.md)) | No |
| Creates dialogs and tasks | Yes | No |
| Confirmation | Asks the user before side-effect tasks | — |
| Runs steps | No | Yes, with a checkpoint after each |
| Side effects | Never | Yes, via actions service with `effect_key` + `fence` |
| On restart | `agent.register`, retries any pending `dialog.create`, rebuilds its chat from events | `agent.register`, then `task.resume`, continues from last checkpoint |

## Shared base (`adapters/base.py`)

Both agents inherit:

- **Bridge client**: sends envelopes through the agent's codec; retries
  `fault_injected` (503), timeouts and connection errors with the **same**
  `request_id`, with backoff 0.1 → 0.2 → 0.4 → … capped at 2 s.
- **Heartbeat loop**: every `HEARTBEAT_SECONDS`, `agent.heartbeat` with the
  tasks it currently owns.
- **Event cursor**: remembers the last `seq` seen; after any reconnect, calls
  `events.since(seq)`. The cursor is in memory only; after a restart it starts
  at 0, which is safe because event handling is idempotent.
- **Crash hooks**: `maybe_crash(point)` reads `CRASH_AT` and calls `os._exit(1)`.
- **Step delay**: `await asyncio.sleep(STEP_DELAY)` between steps (0 in tests).
- **Small HTTP server**: `GET /health`, `GET /state` (recent log lines and
  recent protocol messages in the agent's own format for the dashboard
  panel; polls, heartbeats and empty claims are left out), and agent-specific
  routes below.

Agents use `async` code throughout (`httpx.AsyncClient`, `asyncio.sleep`). A
blocking `time.sleep` would freeze heartbeats and cause false lease expiries.

## Agent A (`adapters/agent_a.py`)

HTTP routes:

| Route | Body | Does |
| --- | --- | --- |
| `POST /message` | `{ "text": "...", "msg_id": "optional" }` | Routes the text, creates the dialog, returns the plan |
| `POST /confirm` | `{ "task_id": "..." }` | `task.confirm` |
| `POST /cancel` | `{ "task_id": "..." }` | `task.cancel` |
| `GET /chat` | — | Chat transcript for the dashboard |
| `POST /retry` | — | Resend the last state-changing request with the same `request_id` (dashboard button 4) |

Flow for one message:

1. `msg_id` = given one, or `msg_{counter}` saved in `data/<mode>/agent_a.json`.
   Before `dialog.create`, the message is also saved there as **pending**, and
   it is removed once the create (and any auto-confirm) is done. This file is
   the one piece of agent-local state: it records *what the user asked*,
   which the bridge cannot know if the create never arrived. After a restart
   Agent A re-routes each pending text (deterministic) and retries
   `dialog.create` with the same `{msg_id}:create`.
2. `router.route(text)` → list of intents with params ([INTENT_ROUTING.md](INTENT_ROUTING.md)).
3. Build tasks from templates; task *n* depends on task *n-1* within one message.
4. `maybe_crash("before_create")`.
5. `dialog.create` with `request_id = {msg_id}:create`. A retry after a lost
   reply gets the same dialog back, never a second one.
6. `maybe_crash("after_create")`.
7. Reply to the user: detected intents, parameters, mode (scripted or LLM), and
   confirm/cancel prompts for side-effect tasks.
8. Follow the event stream; post a chat message when tasks complete, fail or
   are cancelled; post the final summary when the whole dialog is terminal.
   Chat entries have a `kind` (`user`, `plan`, `notice`, `outcome`, `summary`)
   and are keyed so that replaying the whole event log after a restart
   rebuilds the same view without duplicates.

`AUTO_CONFIRM=1` (used by most tests) confirms side-effect tasks immediately.

## Agent B (`adapters/agent_b.py`)

Main loop:

```python
async def run():
    await bridge.register()
    for task in await bridge.resume():          # tasks I owned before a crash
        await work_on(task)
    while True:
        task = await bridge.claim_next()
        if task:
            await work_on(task)
        else:
            await asyncio.sleep(POLL_INTERVAL)

async def work_on(task):
    start = task.last_checkpoint.step_index + 1 if task.last_checkpoint else 0
    for i in range(start, len(task.steps)):
        step = task.steps[i]
        maybe_crash(f"before_{step}")
        if step == task.action:                 # the side-effect step
            output = await do_effect(task)
        else:
            output = await STEPS[step](task)
        maybe_crash(f"after_{step}")
        await bridge.checkpoint(task, i, step, output)
    await bridge.complete(task)
```

Agent B HTTP routes (besides `/health` and `/state`):

| Route | Does |
| --- | --- |
| `POST /pause` / `POST /unpause` | Stop / restart the claim loop (heartbeats continue). Used by S2 |
| `POST /claim` `{ "task_id": "..." }` | Claim that specific task now and work on it. Used by S2 to fire two claims at once |

The side-effect step:

```python
async def do_effect(task):
    key = f"{task.task_id}:{task.action}"            # same key on every attempt
    if task.effect.status == "committed":
        return {"receipt_id": task.effect.receipt_id} # already done and recorded
    await bridge.effect_intent(task, key)             # -> EFFECT_PENDING
    receipt = await actions.perform(key, task.task_id, task.fence, task.action, task.params)
    maybe_crash(f"after_{task.action}_call")         # paid, but the bridge doesn't know yet
    await bridge.effect_result(task, receipt.receipt_id, receipt.replayed)
    return {"receipt_id": receipt.receipt_id, "replayed": receipt.replayed}
```

If the actions call times out or returns 503, B retries **with the same key**.
It never invents a new key.

If any bridge write returns `stale_fence`, `not_owner` or `invalid_transition`,
or the actions service answers `stale_fence`, B **stops working on that task
immediately** and logs it. Someone else owns it now (or it has moved on).

### Step functions

Non-effect steps are deterministic mocks that sleep `STEP_DELAY` and return a
small result, for example `parse` returns the params it sees, `plan` returns
the remaining steps, `search` looks up `adapters/mock_data.json`, `verify`
re-reads the receipt from the actions service.

## Crash points

Set with `CRASH_AT` when the supervisor (re)starts the agent:

| Value | Agent | Dies |
| --- | --- | --- |
| `before_<step>` | B | Just before the step runs, e.g. `before_pay` |
| `after_<step>` | B | Just after the step returns, before its checkpoint, e.g. `after_pay` |
| `after_<action>_call` | B | After the actions service performed the side effect, before B reports it to the bridge, e.g. `after_pay_call`. **The hardest case.** |
| `before_create` / `after_create` | A | Around `dialog.create` |
| `never` (default) | both | — |

`CRASH_MODE=once` (default): the supervisor clears `CRASH_AT` on the next
restart. `CRASH_MODE=always`: it keeps it (used by the retry-limit test).
`os._exit(1)` is used deliberately: no cleanup and no final writes, like a real crash.

## LLM mode (optional, Agent A only)

`ROUTER=llm` swaps only the router; see [INTENT_ROUTING.md](INTENT_ROUTING.md#llm-mode-optional).
Agent B is **always** scripted, because it performs side effects and must be predictable.

## Size estimate

| File | Lines (approx.) |
| --- | --- |
| `base.py` | 150 |
| `agent_a.py` | 120 |
| `agent_b.py` | 120 |
| `router.py` + `templates.py` | 150 |
| `llm_router.py` | 60 |
