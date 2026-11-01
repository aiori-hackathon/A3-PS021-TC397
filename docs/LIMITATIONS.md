# Limitations and Non-Claims

PS-021 explicitly asks teams not to overclaim. This file is the single place
that states what this project is **not**.

## What we do not claim

| We do not claim | What we do claim instead |
| --- | --- |
| An IETF standard, or an implementation of any IETF draft | An **experimental** schema, `dialog-state/0.1-experimental`, inspired by agentproto discussions and labelled on every message |
| Complete MCP interoperability | An **MCP-style** mock format (JSON-RPC tool-call shape) and a codec for it |
| Complete A2A interoperability | An **A2A-style** mock format (task/message shape) and a codec for it, with a documented lossy state mapping |
| Exactly-once delivery | Messages may be delivered more than once; repeats are made harmless. Side effects performed through our actions service happen **at most once per idempotency key** |
| Intelligent agents | Scripted mock adapters with a rule-based intent router; optional LLM routing for Agent A only |

## Assumptions the guarantees depend on

- One bridge process with one SQLite database on a local disk.
- `PRAGMA synchronous = FULL`, so committed transactions survive a hard kill.
- The side-effect service honours idempotency keys and fences (ours does; a
  third-party service might not).
- Agents derive keys from durable IDs, never random values.
- Lease timing uses only the bridge's clock.

## Known limitations

- **Single point of failure:** the bridge. While it is down, no task makes
  progress (nothing is lost, but nothing moves).
- **No authentication:** any process on the machine can call the bridge as
  any agent. Acceptable on localhost; unacceptable on a network.
- **Polling:** workers poll for work; fine for a demo, inefficient at scale.
- **One side effect per task:** multi-effect work must be split into tasks.
- **Rule-based routing:** keyword rules misread unusual phrasing; the dashboard
  shows what was detected so this is visible, never hidden.
- **Disk failure** is not handled; a lost database file means lost state.
- **Clock jumps** on the bridge machine can expire or extend leases incorrectly.
- **Fault injection** is simulated in our own middleware, not at the real network layer.

## Future work

- Map the schema onto a mentor-selected pinned draft, if one is chosen.
- Replicated state (e.g. a shared database or consensus) to remove the single bridge.
- Authentication: signed requests or mutual TLS between agents and bridge.
- Push-based dispatch instead of polling.
- Real MCP and A2A SDK integration behind the same codecs.
- Network-level fault injection (e.g. toxiproxy-style) for more realistic tests.
