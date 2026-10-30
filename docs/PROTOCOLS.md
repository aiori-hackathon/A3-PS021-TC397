# Wire Protocols and Codecs

The two agents deliberately speak two **different** styles, modelled loosely on
MCP and A2A, so the bridge has to translate both into one shared schema. That
translation is our interoperability result.

> These are **"MCP-style"** and **"A2A-style"** mock formats. They borrow the
> general shape of each protocol (JSON-RPC tool calls; tasks with status and
> context) but do not implement either specification completely. We make no
> claim of MCP or A2A conformance.

## MCP-style (Agent A ↔ `POST /mcp`)

JSON-RPC 2.0 shaped. Every bridge operation is a tool call. Dialog metadata
travels in `params._meta`.

Request:

```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "method": "tools/call",
  "params": {
    "name": "dialog.create",
    "arguments": {
      "msg_id": "msg_17",
      "text": "Pay ₹40 to the bakery and email the receipt to Rahul",
      "tasks": [
        { "intent": "payment", "action": "pay", "params": { "amount": 40, "payee": "bakery" },
          "steps": ["parse", "plan", "pay", "verify", "report"], "needs_confirmation": true, "depends_on": [] },
        { "intent": "message", "action": "send", "params": { "to": "Rahul", "body": "receipt" },
          "steps": ["parse", "draft", "send", "report"], "needs_confirmation": true, "depends_on": [1] }
      ]
    },
    "_meta": {
      "schema": "dialog-state/0.1-experimental",
      "request_id": "msg_17:create",
      "sender": "agent_a"
    }
  }
}
```

Success reply:

```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "result": {
    "content": [{ "type": "text", "text": "dialog dlg_3fa91c07d2e1 created with 2 tasks" }],
    "structuredContent": { "dialog_id": "dlg_3fa91c07d2e1", "task_ids": ["tsk_8c1f0a2b9e44", "tsk_02be77c1a913"] },
    "isError": false,
    "_meta": { "event_seq": 12 }
  }
}
```

Error reply (bridge error mapped to JSON-RPC):

```json
{ "jsonrpc": "2.0", "id": 9, "error": { "code": -32001, "message": "invalid_transition",
  "data": { "from": "COMPLETED", "to": "IN_PROGRESS" } } }
```

| Bridge error | JSON-RPC `error.code` |
| --- | --- |
| `validation_error` | -32602 (invalid params) |
| `schema_unsupported` | -32600 (invalid request) |
| `not_found` | -32004 |
| `invalid_transition`, `not_owner`, `stale_fence`, `already_claimed` | -32001 |
| `request_id_conflict` | -32002 |
| `fault_injected` | not encoded; HTTP 503 is returned before the codec runs |

Note: the JSON-RPC `id` only matches a reply to its request on one connection.
It is **not** an idempotency key. The idempotency key is `_meta.request_id`.

Tools Agent A uses: `dialog.create`, `dialog.get`, `task.confirm`,
`task.cancel`, `events.since`, `agent.register`, `agent.heartbeat`.

## A2A-style (Agent B ↔ `POST /a2a`)

Message-and-task shaped. The dialog is the `contextId`; each operation is a
message with a `kind`.

Request:

```json
{
  "message": {
    "messageId": "tsk_8c1f0a2b9e44:checkpoint:2:1",
    "role": "agent",
    "kind": "task.checkpoint",
    "contextId": "dlg_3fa91c07d2e1",
    "taskId": "tsk_8c1f0a2b9e44",
    "parts": [{ "kind": "data", "data": { "step_index": 2, "step": "pay", "output": { "receipt_id": "rcp_51d0" } } }],
    "metadata": { "schema": "dialog-state/0.1-experimental", "sender": "agent_b", "fence": 1 }
  }
}
```

Reply (a task object):

```json
{
  "task": {
    "id": "tsk_8c1f0a2b9e44",
    "contextId": "dlg_3fa91c07d2e1",
    "status": { "state": "working", "timestamp": "2026-11-02T10:15:03.125Z" },
    "metadata": { "bridge_state": "EFFECT_PENDING", "fence": 1, "attempt": 1,
                  "effect": { "key": "tsk_8c1f0a2b9e44:pay", "status": "committed" }, "event_seq": 43 }
  }
}
```

Error reply:

```json
{ "error": { "code": "stale_fence", "message": "fence 1 < current 2", "details": { "current_fence": 2 } } }
```

Here `messageId` **is** the idempotency key, which matches the A2A-style idea
that a message has a unique ID.

Kinds Agent B uses: `agent.register`, `agent.heartbeat`, `task.claim_next`,
`task.claim`, `task.resume`, `task.checkpoint`, `task.effect_intent`,
`task.effect_result`, `task.complete`, `task.fail`, `events.since`.

## Field mapping

| Shared envelope | MCP-style | A2A-style |
| --- | --- | --- |
| `schema` | `params._meta.schema` | `message.metadata.schema` |
| `request_id` | `params._meta.request_id` | `message.messageId` |
| `sender` | `params._meta.sender` | `message.metadata.sender` |
| `op` | `params.name` | `message.kind` |
| `dialog_id` | `arguments.dialog_id` | `message.contextId` |
| `task_id` | `arguments.task_id` | `message.taskId` |
| `fence` | `arguments.fence` | `message.metadata.fence` |
| `payload` | remaining `arguments` | `message.parts[0].data` |
| `sent_at` | `params._meta.sent_at` | `message.metadata.sent_at` |
| task `state` | `structuredContent.state` (exact bridge state) | `status.state` (mapped) + `metadata.bridge_state` (exact) |

State mapping for A2A-style is in [STATE_MACHINE.md](STATE_MACHINE.md#mapping-to-a2a-style-task-states).

## Codec contract

Each codec module exposes six pure functions (no I/O, easy to unit-test). The
bridge uses the decode-request / encode-reply side; the mock adapters use the
encode-request / decode-reply side of the same module, so the two cannot drift.

```python
def encode_request(env: Envelope) -> dict          # adapters
def decode_request(native: dict) -> Envelope       # bridge
def encode_reply(reply: Reply, native_request: dict) -> dict   # bridge
def decode_reply(native: dict) -> Reply            # adapters
def encode_task_view(task: Task) -> dict          # for the inspector
def decode_task_view(native: dict) -> Task         # for round-trip tests
```

Details that keep the round trips lossless:

- Task views carry the **full** task record: MCP-style in `structuredContent`,
  A2A-style in `metadata` next to `bridge_state`.
- MCP-style errors put the bridge error code in `error.message` (as above) and
  the human-readable text in `error.data._message`.
- A2A-style success replies are `{"task": view}` when the result is one task
  (with `metadata.event_seq`), `{"tasks": [views], "event_seq": n}` for a
  list of tasks, and `{"result": ..., "event_seq": n}` otherwise.

Round-trip property tested in I1 and I2:

```
decode_request(encode(x)) == x        for every valid envelope x
decode_task_view(encode_task_view(t)) == t   for every task t, in every state
```

## Known interoperability limits

- SUSPENDED has no A2A-style equivalent; preserved via `metadata.bridge_state`.
- MCP-style has no native task lifecycle; the bridge's state is returned as
  data, not as protocol-level status.
- Streaming, push notifications, authentication and discovery from the real
  protocols are out of scope.
