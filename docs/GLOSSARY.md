# Glossary

Plain-language meanings of the terms used in this project.

| Term | Meaning |
| --- | --- |
| **Agent** | A program that does work. Here: Agent A (plans) and Agent B (does). |
| **Adapter** | The part of an agent that speaks a particular protocol style. "Mock adapter" = a simple fake one for testing. |
| **A2A-style** | Our message format modelled loosely on Google's Agent2Agent protocol: tasks with a status and a context ID. |
| **Atomic** | Happens completely or not at all; nothing can see it half-done. |
| **Bridge (Dialog Bridge)** | Our central server that tracks every task and protects against duplicates. |
| **Checkpoint** | A saved record of "step N is done, here is its output", so work can continue after a crash. |
| **Claim** | An agent saying "I'll take this task". Only one claim can win. |
| **Codec** | A translator between a native message format and our shared schema. |
| **Dialog** | One conversation, started by one user message. May contain several tasks. |
| **Dedup (deduplication)** | Recognising a repeat and not acting on it twice. |
| **Effect key** | The idempotency key for a side effect, `task_id:action`. |
| **Endpoint / route** | An address on a server for one job, e.g. `POST /a2a`. |
| **Event / event log** | A numbered record of everything that happened, in order. |
| **Exactly-once** | Something happening exactly one time no matter what. Impossible to guarantee in general over unreliable networks; we don't claim it. |
| **FastAPI / Uvicorn** | The Python library we write servers with / the program that runs them. |
| **Fence (fencing token)** | A number that goes up on every claim. Writes with an old number are rejected, so a slow old owner can't interfere. |
| **Fault injection** | Breaking things on purpose (drop, delay, duplicate, cut) to test recovery. |
| **Heartbeat** | A regular "I'm still alive" message from an agent. |
| **Idempotent** | Doing it twice has the same effect as doing it once. |
| **Idempotency key / request_id** | A label on a request so a repeat can be recognised. |
| **Inbox** | The bridge's table of processed request IDs and their stored replies. |
| **Intent router** | The rules that decide what kind of task a user's text is (payment, message, …). |
| **Lease** | A time-limited claim. If the owner stops sending heartbeats, the lease expires. |
| **Ledger** | The actions service's record of every side effect performed. |
| **Lifecycle / state machine** | The list of states a task can be in and the allowed moves between them. |
| **localhost / port** | "This computer" / a numbered door a server listens on. |
| **MCP-style** | Our message format modelled loosely on Anthropic's Model Context Protocol: JSON-RPC tool calls. |
| **Orchestrator** | The agent that plans and hands out work (Agent A). |
| **Pydantic** | Python library that defines data shapes and checks messages against them. |
| **Reclaim** | A different agent claiming a suspended task. |
| **Resume** | The same agent continuing its own task after restarting. |
| **Schema** | The agreed shape of every message and record. Ours is `dialog-state/0.1-experimental`. |
| **Side effect** | Something that changes the outside world: paying, sending, booking. Dangerous to repeat. |
| **SQLite / WAL** | A database stored in one file / its crash-safe write mode. |
| **Supervisor** | Our program that starts, kills, restarts and resets all the others. |
| **Task** | One unit of work with one ID that never changes. |
| **Transaction** | A group of database writes that are saved together or not at all. |
| **WebSocket** | A connection that stays open so the server can push updates to the dashboard. |
| **Worker** | The agent that executes tasks (Agent B, and B2 in some tests). |
