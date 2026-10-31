# Configuration

All settings are environment variables with defaults per mode. The supervisor
passes them to every child process. `MODE=demo` or `MODE=test` selects the
default column.

## Timing

| Variable | Demo | Test | Meaning |
| --- | --- | --- | --- |
| `LEASE_SECONDS` | 6 | 2 | How long a claim lasts without a heartbeat. Short in demo so a crash is detected visibly within seconds |
| `HEARTBEAT_SECONDS` | 1 | 0.5 | Heartbeat interval; must be well below the lease |
| `WATCHDOG_TICK` | 0.5 | 0.2 | How often the watchdog checks leases |
| `DISPATCH_TICK` | 0.5 | 0.1 | How often the dispatcher runs |
| `STARTUP_GRACE` | 6 | 2 | Lease extension given to in-flight tasks after a bridge restart |
| `POLL_INTERVAL` | 1 | 0.1 | Worker idle poll interval |
| `STEP_DELAY` | 1.5 | 0 | Pause per step so the dashboard is readable |
| `HTTP_TIMEOUT` | 5 | 1 | Client timeout for every HTTP call |
| `MAX_ATTEMPTS` | 3 | 3 | Attempts before a task becomes FAILED |
| `RETRY_BACKOFF_MAX` | 2 | 0.5 | Cap for client retry backoff (seconds) |

## Ports and paths

| Variable | Demo | Test |
| --- | --- | --- |
| `BRIDGE_PORT` | 8000 | 9000 |
| `AGENT_A_PORT` | 8001 | 9001 |
| `AGENT_B_PORT` | 8002 | 9002 |
| `ACTIONS_PORT` | 8003 | 9003 |
| `AGENT_B2_PORT` | 8004 | 9004 |
| `SUPERVISOR_PORT` | 8009 | 9009 |
| `DATA_DIR` | `data/demo` | `data/test` |
| `LOG_DIR` | `logs/demo` | `logs/test` |

## Behaviour switches

| Variable | Default | Meaning |
| --- | --- | --- |
| `AGENT_ID` | per process | `agent_a`, `agent_b`, `agent_b2` |
| `CRASH_AT` | `never` | Crash point ([ADAPTERS.md](ADAPTERS.md#crash-points)) |
| `CRASH_MODE` | `once` | `once` or `always` |
| `AUTO_CONFIRM` | demo `0`, test `1` | Agent A confirms side-effect tasks automatically |
| `ROUTER` | `rules` | `rules` or `llm` (Agent A only) |
| `OLLAMA_URL` | `http://localhost:11434` | LLM mode only |
| `LLM_MODEL` | `qwen3.5:4b` | LLM mode only |
| `LLM_TIMEOUT` | 8 | Seconds before falling back to rules |
| `DISABLE_DEDUP` | `0` | **Testing only.** Turns off inbox lookups, one-dialog-per-`msg_id`, and ledger key checks |
| `AUTO_RESTART` | `0` | Supervisor restarts crashed agents automatically |
| `AUTO_RESTART_DELAY` | 2 | Seconds |
| `CORS_ORIGINS` | `http://localhost:8000,http://127.0.0.1:8000` | Origins allowed to call agents, actions and supervisor |
| `TEST_MODE` | `fast` | For pytest: `fast` or `watch` |
| `CHAOS_SEED` | `42` | Random seed for the chaos run |
| `RUN_ID` | timestamp | Evidence folder name under `results/`; share it across pytest runs to build one RESULTS.md |

## Invariants to keep

- `HEARTBEAT_SECONDS × 3 ≤ LEASE_SECONDS`, so one missed heartbeat never
  expires a lease.
- `HTTP_TIMEOUT < LEASE_SECONDS`, so a single slow call cannot by itself
  expire a lease.
- In watch mode, test timeouts are multiplied by 5.
