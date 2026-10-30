# Dialog Bridge — AIORI-3 · PS-021

**Experimental dialog correlation and recovery across two mock agent adapters.**

Team repository: `A3-PS021-TC397` · Category: 6G & Future Networks

> **Status: research prototype.** This project uses an **experimental** schema,
> `dialog-state/0.1-experimental`, inspired by discussions in the IETF
> [agentproto](https://datatracker.ietf.org/group/agentproto/about/) group.
> It is **not** an IETF standard, **not** a complete MCP or A2A implementation,
> and does **not** provide universal exactly-once delivery.
> See [docs/LIMITATIONS.md](docs/LIMITATIONS.md).

---

## The problem in one paragraph

When one AI agent hands work to another and something goes wrong (an agent
crashes, a network link drops, a reply is lost), work is either **lost** or
**done twice**. Doing it twice is dangerous when the work has a side effect,
such as a payment. PS-021 asks for a prototype that tracks every task with
explicit IDs and lifecycle states, rejects duplicate requests, and recovers
durably after restarts, demonstrated across five disconnect/retry scenarios.

## Our answer in one paragraph

A **Dialog Bridge** sits between two mock agents. It is a Python (FastAPI)
server with its own SQLite database. Every message from either agent passes
through it. The bridge gives every conversation a `dialog_id` and every unit of
work a `task_id`, moves tasks through an enforced state machine, stores every
request's reply so repeats get the same answer, hands out leases and fencing
tokens so only one agent can work on a task at a time, and logs every change as
a numbered event. Side effects (payments, messages, bookings) go to a mock
**actions service** that refuses to repeat an idempotency key it has already
seen. When an agent or the bridge itself restarts, it reloads state from SQLite
and continues the same task, with the same ID, without repeating side effects.

## The pieces

| Process | Demo port | Test port | Role |
| --- | --- | --- | --- |
| Dialog Bridge + dashboard | 8000 | 9000 | State, schema, state machine, dedup, leases, events, fault proxy, codecs |
| Agent A — orchestrator | 8001 | 9001 | MCP-style adapter; rule-based intent router; plans and delegates |
| Agent B — worker | 8002 | 9002 | A2A-style adapter; runs scripted steps; performs side effects |
| Actions service | 8003 | 9003 | Mock side effects (payment, message, booking) with an idempotent ledger |
| Agent B2 — second worker | 8004 | 9004 | Started only for the race and slow-worker tests |
| Supervisor | 8009 | 9009 | Starts, kills, restarts and resets everything; runs tests |
| Ollama (optional) | 11434 | — | Only for the optional LLM router mode of Agent A |

## Requirement → where it is met

| PS-021 requirement / expected outcome | Where |
| --- | --- |
| Two mock agent adapters | [docs/ADAPTERS.md](docs/ADAPTERS.md), `adapters/` |
| One defined dialog-state schema | [docs/SCHEMA.md](docs/SCHEMA.md), `schema/dialog-state-0.1.json` |
| Explicit lifecycle states and valid transitions | [docs/STATE_MACHINE.md](docs/STATE_MACHINE.md), `bridge/state_machine.py` |
| Preserve task identity across adapter restarts | [docs/DEDUP_AND_RECOVERY.md](docs/DEDUP_AND_RECOVERY.md) |
| Request deduplication, no duplicate side effects | [docs/DEDUP_AND_RECOVERY.md](docs/DEDUP_AND_RECOVERY.md), [docs/ACTIONS_SERVICE.md](docs/ACTIONS_SERVICE.md) |
| Durable state / recovery mechanism | [docs/BRIDGE.md](docs/BRIDGE.md), [docs/DEDUP_AND_RECOVERY.md](docs/DEDUP_AND_RECOVERY.md) |
| Five disconnect/retry test scenarios | [docs/TESTING.md](docs/TESTING.md) (S1–S5) |
| Interoperability and recovery test results | [docs/TESTING.md](docs/TESTING.md), [docs/RESULTS.md](docs/RESULTS.md) |
| Demonstration of identity preservation and duplicate rejection | [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md), [docs/DASHBOARD.md](docs/DASHBOARD.md) |
| Clearly labelled experimental schema | `schema` field on every message; [docs/SCHEMA.md](docs/SCHEMA.md) |
| No overclaiming | [docs/LIMITATIONS.md](docs/LIMITATIONS.md) |

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python supervisor.py --mode demo   # starts everything
# open http://localhost:8000 for the dashboard
```

Run the tests (isolated test world, about a minute):

```bash
TEST_MODE=fast pytest              # Windows PowerShell: $env:TEST_MODE="fast"; pytest
```

Watch one scenario live on the dashboard:

```bash
TEST_MODE=watch pytest -k s4
```

## Documentation map

| File | What it covers |
| --- | --- |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Big picture: processes, request path, diagrams |
| [docs/SCHEMA.md](docs/SCHEMA.md) | The experimental dialog-state schema |
| [docs/IDENTIFIERS.md](docs/IDENTIFIERS.md) | Every ID in the system: who creates it and why |
| [docs/STATE_MACHINE.md](docs/STATE_MACHINE.md) | Task states and the allowed transitions |
| [docs/BRIDGE.md](docs/BRIDGE.md) | Inside the bridge: components, database, API |
| [docs/PROTOCOLS.md](docs/PROTOCOLS.md) | MCP-style and A2A-style wire formats and codecs |
| [docs/ADAPTERS.md](docs/ADAPTERS.md) | Agent A and Agent B: roles, loops, crash points |
| [docs/INTENT_ROUTING.md](docs/INTENT_ROUTING.md) | Rule-based intent router and task templates |
| [docs/DEDUP_AND_RECOVERY.md](docs/DEDUP_AND_RECOVERY.md) | How duplicates are stopped and how recovery works |
| [docs/ACTIONS_SERVICE.md](docs/ACTIONS_SERVICE.md) | The mock side-effect service and its ledger |
| [docs/FAULT_INJECTION.md](docs/FAULT_INJECTION.md) | Breaking things on purpose: rule format |
| [docs/SUPERVISOR.md](docs/SUPERVISOR.md) | Starting, killing, restarting, resetting |
| [docs/CONFIG.md](docs/CONFIG.md) | Every environment variable and timing value |
| [docs/TESTING.md](docs/TESTING.md) | Test harness, all scenarios, modes, evidence |
| [docs/DASHBOARD.md](docs/DASHBOARD.md) | The web dashboard: tabs, panels, live updates |
| [docs/RESULTS.md](docs/RESULTS.md) | Results template, filled from real test runs |
| [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) | The live demo, minute by minute |
| [docs/BUILD_PLAN.md](docs/BUILD_PLAN.md) | Build phases, team split, checklists |
| [docs/LIMITATIONS.md](docs/LIMITATIONS.md) | What we do not claim, known limits, future work |
| [docs/GLOSSARY.md](docs/GLOSSARY.md) | Plain-language meaning of every term |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Git workflow for the team |
| [CLAUDE.md](CLAUDE.md) | Rules for AI coding assistants working in this repo |

## Repository layout

```
A3-PS021-TC397/
├── bridge/
│   ├── main.py              # FastAPI app, routes, WebSocket, serves dashboard
│   ├── db.py                # SQLite setup (WAL), tables, transaction helper
│   ├── models.py            # Pydantic models = dialog-state schema 0.1
│   ├── state_machine.py     # allowed transitions; everything else rejected
│   ├── inbox.py             # request dedup (stored replies)
│   ├── leases.py            # claim, heartbeat, fence
│   ├── events.py            # numbered event log + WebSocket fan-out
│   ├── registry.py          # agent registry
│   ├── metrics.py           # counters for results
│   ├── proxy.py             # fault-injection middleware
│   ├── jobs.py              # watchdog, dispatcher, startup recovery
│   ├── handlers.py          # request path: decode, inbox, op, stored reply
│   ├── tasks.py             # dialog and task operations
│   ├── errors.py            # BridgeError -> error reply
│   └── codecs/
│       ├── mcp_style.py     # JSON-RPC style <-> shared schema
│       └── a2a_style.py     # task/message style <-> shared schema
├── adapters/
│   ├── base.py              # shared loop, checkpoints, heartbeats, crash hooks
│   ├── agent_a.py           # orchestrator (MCP-style)
│   ├── agent_b.py           # worker (A2A-style)
│   ├── router.py            # rule-based intent router
│   ├── templates.py         # step lists per intent
│   ├── llm_router.py        # optional Ollama router
│   └── mock_data.json       # data for the scripted search step
├── services/
│   ├── actions.py           # mock side effects + ledger
│   └── faults.py            # shared fault-rule engine
├── dashboard/  index.html  app.js  style.css
├── schema/dialog-state-0.1.json
├── tests/                   # see docs/TESTING.md
├── scripts/build_results.py # results/ -> docs/RESULTS.md tables
├── supervisor.py
├── config.py               # env vars with per-mode defaults (docs/CONFIG.md)
├── requirements.txt
├── pytest.ini
├── .github/workflows/tests.yml
└── docs/
```

## Team

| Role | Owns |
| --- | --- |
| Person 1 — core | Schema, state machine, database, inbox, leases, events, jobs, tests |
| Person 2 — edges | Codecs, both adapters, router, actions service, supervisor, dashboard |

## References

- IETF agentproto working group: https://datatracker.ietf.org/group/agentproto/about/
- Problem statement: AIORI-3 PS-021, *Experimental Dialog Correlation and Recovery Across Agent Adapters*
