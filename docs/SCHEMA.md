# Dialog-State Schema `0.1-experimental`

> **Label:** `dialog-state/0.1-experimental`. This is our own experimental
> schema, inspired by discussions in the IETF agentproto working group. It is
> not a standard and not a draft of one. If the mentor selects a pinned draft,
> this file is where the mapping to it will be documented, and the version
> string will change.

The schema has three parts:

1. **Envelope**: the shape of every request inside the bridge, after a codec
   has translated it from MCP-style or A2A-style.
2. **Records**: what the bridge stores for dialogs, tasks and events.
3. **Replies and errors**: what comes back.

The Pydantic models in `bridge/models.py` are the source of truth. The file
`schema/dialog-state-0.1.json` is generated from them
(`python -m bridge.models --export`), never edited by hand.

## 1. Envelope

```json
{
  "schema": "dialog-state/0.1-experimental",
  "request_id": "tsk_8c1f0a2b9e44:checkpoint:2:1",
  "sender": "agent_b",
  "op": "task.checkpoint",
  "dialog_id": "dlg_3fa91c07d2e1",
  "task_id": "tsk_8c1f0a2b9e44",
  "fence": 1,
  "payload": { "step_index": 2, "step": "pay", "output": { "receipt_id": "rcp_51d0" } },
  "sent_at": "2026-11-02T10:15:03.120Z"
}
```

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `schema` | string | yes | Must equal a version the bridge supports, otherwise `400 schema_unsupported` |
| `request_id` | string | for every op that changes state | Idempotency key for this request; see [IDENTIFIERS.md](IDENTIFIERS.md) |
| `sender` | string | yes | Registered agent ID, e.g. `agent_a`, `agent_b` |
| `op` | string | yes | Operation name, from the table below |
| `dialog_id` | string or null | depends on op | Conversation the request belongs to |
| `task_id` | string or null | depends on op | Task the request is about |
| `fence` | integer or null | for task writes by a worker | Fencing token from the sender's claim |
| `payload` | object | yes (may be empty) | Op-specific data |
| `sent_at` | ISO-8601 string | yes | Sender's clock; informational only, never used for leases |

### Operations

| `op` | Sender | Changes state? | Payload | Result |
| --- | --- | --- | --- | --- |
| `agent.register` | any agent | yes | `protocol`, `base_url` | agent record |
| `agent.heartbeat` | any agent | yes | `task_ids` it is working on | lease expiry times |
| `dialog.create` | Agent A | yes | `msg_id`, `text`, `tasks[]` (intent, params, steps, action, needs_confirmation, depends_on); `depends_on` holds 1-based indexes of earlier tasks in the same request | dialog + task records |
| `dialog.get` | any | no | — | dialog + tasks |
| `task.confirm` | Agent A | yes | — | task |
| `task.cancel` | Agent A | yes | `reason` | task |
| `task.claim_next` | worker | yes | — | claimed task + fence + lease, or `null` |
| `task.claim` | worker | yes | — | claimed task + fence + lease (used by the race test) |
| `task.resume` | worker | yes | — | all tasks this agent owned, with last checkpoints, re-claimed with new fences |
| `task.checkpoint` | worker (owner) | yes | `step_index`, `step`, `output` | task |
| `task.effect_intent` | worker (owner) | yes | `effect_key`, `action`, `params` | task (now EFFECT_PENDING) |
| `task.effect_result` | worker (owner) | yes | `receipt_id`, `replayed` | task |
| `task.complete` | worker (owner) | yes | `result` | task |
| `task.fail` | worker (owner) | yes | `reason`, `retryable` | task: FAILED, or SUSPENDED if `retryable` |
| `events.since` | any | no | `seq` | list of events after `seq` |

## 2. Records

### Dialog

```json
{
  "dialog_id": "dlg_3fa91c07d2e1",
  "msg_id": "msg_17",
  "text": "Pay ₹40 to the bakery and email the receipt to Rahul",
  "created_by": "agent_a",
  "task_ids": ["tsk_8c1f0a2b9e44", "tsk_02be77c1a913"],
  "created_at": "2026-11-02T10:14:58.004Z"
}
```

### Task

```json
{
  "task_id": "tsk_8c1f0a2b9e44",
  "dialog_id": "dlg_3fa91c07d2e1",
  "index": 1,
  "intent": "payment",
  "action": "pay",
  "params": { "amount": 40, "currency": "INR", "payee": "bakery" },
  "steps": ["parse", "plan", "pay", "verify", "report"],
  "state": "EFFECT_PENDING",
  "owner": "agent_b",
  "lease_until": "2026-11-02T10:15:09.120Z",
  "fence": 1,
  "attempt": 1,
  "max_attempts": 3,
  "depends_on": [],
  "needs_confirmation": true,
  "confirmed": true,
  "effect": { "key": "tsk_8c1f0a2b9e44:pay", "status": "intent", "receipt_id": null, "replayed": false },
  "last_checkpoint": { "step_index": 1, "step": "plan", "output": {} },
  "result": null,
  "reason": null,
  "created_at": "2026-11-02T10:14:58.010Z",
  "updated_at": "2026-11-02T10:15:03.125Z"
}
```

`state` is one of the states in [STATE_MACHINE.md](STATE_MACHINE.md).
`effect.status` is `none`, `intent` or `committed`.
A task has **at most one** side effect. Multi-effect requests become multiple tasks.
`depends_on` lists the `index` values of earlier tasks in the same dialog.

### Event

```json
{
  "seq": 42,
  "ts": "2026-11-02T10:15:03.125Z",
  "dialog_id": "dlg_3fa91c07d2e1",
  "task_id": "tsk_8c1f0a2b9e44",
  "type": "effect_intent",
  "actor": "agent_b",
  "data": { "effect_key": "tsk_8c1f0a2b9e44:pay", "fence": 1 }
}
```

Event types:

| Group | Types |
| --- | --- |
| Lifecycle | `dialog_created`, `task_created`, `state_changed`, `confirmed`, `cancelled`, `claimed`, `resumed`, `checkpoint`, `effect_intent`, `effect_committed`, `completed`, `failed` |
| Protection | `duplicate_request`, `duplicate_rejected` (actions replayed a receipt), `stale_fence_blocked`, `invalid_transition_rejected`, `schema_rejected`, `request_id_conflict` |
| Health | `agent_registered`, `agent_unresponsive`, `lease_expired`, `reclaimed`, `max_attempts_reached`, `bridge_started` |
| Testing | `fault_rule_set`, `fault_applied`, `test_step` (captions for watch mode) |

## 3. Replies and errors

Success:

```json
{ "ok": true, "result": { "...": "..." }, "event_seq": 42 }
```

Error:

```json
{ "ok": false, "error": { "code": "stale_fence", "message": "fence 1 < current 2", "details": { "current_fence": 2 } } }
```

| HTTP | `code` | When |
| --- | --- | --- |
| 400 | `schema_unsupported` | Unknown `schema` value |
| 400 | `validation_error` | Envelope or payload does not match the schema |
| 404 | `not_found` | Unknown dialog or task |
| 409 | `invalid_transition` | Move not allowed from the current state |
| 409 | `not_owner` | Sender does not hold the lease |
| 409 | `stale_fence` | Sender's fence is lower than the current fence |
| 409 | `already_claimed` | Another agent holds a valid lease |
| 422 | `request_id_conflict` | Same `request_id` reused with a different body |
| 503 | `fault_injected` | The fault proxy dropped this request (header `X-Fault`) |

A **stored reply** is returned unchanged for a repeated `request_id`, including
stored errors. Repeating a request never produces a different outcome.

## Versioning

- Every envelope carries `schema`. The bridge supports exactly one value today.
- Any change to field meaning means a new version string.
- Unknown versions are rejected, never guessed at.
