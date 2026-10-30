# Build Plan

Build bottom-up and test-first: **get scenarios passing from the command line
before building the dashboard.** A good-looking dashboard on top of broken
recovery is the most common hackathon trap.

## Team split

| Person 1 — core | Person 2 — edges |
| --- | --- |
| `bridge/models.py`, `state_machine.py`, `db.py`, `inbox.py`, `leases.py`, `events.py`, `jobs.py`, `metrics.py`, `registry.py` | `bridge/codecs/*`, `bridge/proxy.py`, `services/faults.py`, `services/actions.py` |
| `tests/` harness, S0–S5, R1–R4, M1, C1 | `adapters/*` (base, A, B, router, templates), `supervisor.py` |
| `scripts/build_results.py`, RESULTS.md | `dashboard/*`, DEMO_SCRIPT rehearsal |
| **Together:** integration, I1–I3, X1–X7, proposal PDF, pseudocode slide, rehearsals |

Agree on [SCHEMA.md](SCHEMA.md) and [IDENTIFIERS.md](IDENTIFIERS.md) **before
writing code**. They are the contract between the two halves.

## Phases

### Phase 0 — Setup
- [ ] Decide with the team what happens to existing content in the repository; start the Python project on a clean branch
- [ ] Python 3.11+, virtual environment, `requirements.txt` (pin exact versions once installed)
- [ ] Folder layout from the README; empty modules with docstrings
- [ ] `pytest.ini`, `.gitignore` (`data/`, `logs/`, `results/`, `.venv/`, `__pycache__/`)
- [ ] Copy these docs into `docs/`
- **Done when:** `pytest` runs (0 tests) and `uvicorn bridge.main:app` serves `/health`.

### Phase 1 — Core logic without servers
- [ ] Pydantic models; export JSON Schema to `schema/dialog-state-0.1.json`
- [ ] State machine table + guards; `tests/unit/test_state_machine.py`
- [ ] Router + templates; `tests/unit/test_router.py` (table from INTENT_ROUTING)
- [ ] Codecs; `tests/unit/test_codecs.py` (I1, I2)
- **Done when:** all unit tests pass.

### Phase 2 — Bridge
- [ ] `db.py` with WAL, `synchronous=FULL`, tables, `transaction()`
- [ ] Inbox, leases (claim SQL), events, registry
- [ ] Dispatcher (moved here from Phase 3: the hand walk below needs it)
- [ ] `/mcp` and `/a2a` entrances with codecs; read-only routes; `/ws`
- [ ] Rules tests R1–R4 against the running bridge (call `/a2a` and `/mcp` directly)
- **Done when:** R1–R4 pass; `/docs` lets you create a dialog and walk a task to COMPLETED by hand.

### Phase 3 — Actions service and happy path
- [ ] `services/actions.py` with ledger and fences
- [ ] `adapters/base.py`, Agent A, Agent B (no crash points yet)
- [ ] Jobs: watchdog, startup recovery (the dispatcher is built in Phase 2)
- [ ] `supervisor.py`: start order, `/status`, `/reset`
- [ ] Test harness: clients, `wait_until`, `world` fixture; **S0**
- **Done when:** S0 passes 20 times in a row.

### Phase 4 — Failure handling
- [ ] Crash points; supervisor `/kill`, `/restart` with env
- [ ] `task.resume`, early-resume path, effect protocol with `after_<action>_call`
- [ ] Fault rules in bridge proxy and actions service
- [ ] **S1–S5**, then **X1–X7**, then **I3**
- **Done when:** `pytest -m "official or extra" --count=20` passes with no failures.

### Phase 5 — Proof
- [ ] `DISABLE_DEDUP` switch; **M1**
- [ ] **C1** chaos run
- [ ] Evidence export, `scripts/build_results.py`, RESULTS.md generated
- [ ] GitHub Actions workflow
- **Done when:** RESULTS.md is fully generated from one run.

### Phase 6 — Dashboard
- [ ] Live tab: chat, panels, task view, ledger, controls, link status, reconnect banner
- [ ] Tests tab: runner, modes, protection switch, chaos counters, metrics
- [ ] Inspect tab: inspector, event log filters, message flow, replay
- [ ] Watch-mode captions
- **Done when:** the full DEMO_SCRIPT runs without touching a terminal.

### Phase 7 — Optional LLM mode
- [ ] `llm_router.py` with validation and fallback
- [ ] Try on the actual demo machine
- **Skip entirely if time is short.** Nothing else depends on it.

### Phase 8 — Submission
- [ ] Proposal PDF in the organisers' structure (`Proposed-structure-hackathon.pdf`), uploaded to the repo
- [ ] Pseudocode snippet for the mentor (S4 test + effect protocol)
- [ ] README links verified; LIMITATIONS reviewed
- [ ] Two full rehearsals with a timer; screen recording made
- [ ] Run the full suite on the demo laptop on the day

## Size estimate

| Area | Lines (approx.) |
| --- | --- |
| Bridge (incl. codecs, jobs) | 700–900 |
| Adapters + router | 500–600 |
| Actions + faults + supervisor | 350–450 |
| Tests | 600–800 |
| Dashboard | 600–900 |

## Definition of done (whole project)

- [ ] All PS-021 key requirements mapped in README with working links
- [ ] S1–S5 pass 20/20 on the demo laptop
- [ ] M1 shows duplicates with protection off
- [ ] RESULTS.md generated, not hand-written
- [ ] Every message carries `schema: dialog-state/0.1-experimental`
- [ ] No sentence anywhere claims a standard, full MCP/A2A, or exactly-once delivery
