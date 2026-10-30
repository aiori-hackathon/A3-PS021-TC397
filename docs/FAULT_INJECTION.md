# Fault Injection

To prove recovery works, the system has to break on command. Faults are
injected in exactly two places, both using the same rule engine
(`services/faults.py`):

| Where | Covers links |
| --- | --- |
| Bridge proxy middleware | `agent_a` (traffic on `/mcp`), `agent_b`, `agent_b2` (traffic on `/a2a`, by sender) |
| Actions service middleware | `actions` (worker → actions service) |

Process crashes are **not** faults in this sense; they are done by the
supervisor ([SUPERVISOR.md](SUPERVISOR.md)) and crash points ([ADAPTERS.md](ADAPTERS.md#crash-points)).

## Rule format

```json
{
  "id": "r1",
  "link": "agent_a",
  "action": "drop_reply",
  "match": { "op": "dialog.create" },
  "count": 1,
  "delay_ms": 0,
  "note": "S1: lose the reply to dialog.create once"
}
```

| Field | Meaning |
| --- | --- |
| `link` | `agent_a`, `agent_b`, `agent_b2`, `actions` |
| `action` | See below |
| `match` | Optional filter: `op` (bridge) or `action` (actions service). Empty = every request on the link |
| `count` | How many times the rule fires before it disappears. Default **1**. `-1` = until deleted |
| `delay_ms` | For `delay` |
| `note` | Shown on the dashboard and in the event log |

## Actions

| Action | What happens | What the client sees | Tests |
| --- | --- | --- | --- |
| `drop_request` | Request discarded before the handler | HTTP 503, header `X-Fault: dropped` | General retry |
| `drop_reply` | Handler runs and **commits**; reply discarded | HTTP 503, header `X-Fault: reply-dropped` | S1, X1 |
| `delay` | Waits `delay_ms` before the handler | Slow reply (may time out) | X2 |
| `duplicate` | Handler called twice with the same body | One reply (the second call hits the inbox) | X7 |
| `cut` | Every request on the link dropped while active | HTTP 503 `X-Fault: link-cut` | S5, dashboard Disconnect |

Clients must treat **any** 503, timeout or connection error as "outcome
unknown" and retry with the **same** `request_id` / `effect_key`. They must not
read the `X-Fault` header to decide what to do; it is there only for logs and
the dashboard.

Why `drop_reply` returns 503 instead of hanging: it is deterministic and fast
for tests, and from the client's point of view it means the same thing as a
timeout: "I don't know if it worked."

## API

On both the bridge and the actions service:

| Route | Does |
| --- | --- |
| `POST /faults` | Add a rule; returns it with an `id` |
| `GET /faults` | Active rules with remaining counts |
| `DELETE /faults/{id}` | Remove one rule |
| `DELETE /faults` | Remove all rules |

Every application of a rule logs a `fault_applied` event (bridge) or a log line
(actions service) with the rule's `note`.

## Lifetime

Rules live in memory. They disappear when the process restarts, and the
supervisor's `/reset` clears them too. Tests that restart the bridge re-apply
any rule they still need afterwards (S5 does this deliberately).

## Dashboard mapping

| Button | Rule |
| --- | --- |
| Disconnect A ↔ Bridge | `{ "link": "agent_a", "action": "cut", "count": -1 }`; pressing again deletes it |
| Drop next reply to A | `{ "link": "agent_a", "action": "drop_reply" }` |
| Slow Agent B | `{ "link": "agent_b", "action": "delay", "delay_ms": 3 × lease, "count": -1 }` |
| Lose payment reply | `{ "link": "actions", "action": "drop_reply", "match": { "action": "pay" } }` |
