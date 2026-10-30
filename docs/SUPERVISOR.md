# Supervisor

`supervisor.py` starts every process, can kill or restart any of them on
command, resets a world to empty, and runs the test suite. Nothing else can do
these jobs: a crashed agent cannot restart itself, and the bridge cannot
restart itself during scenario S5.

```bash
python supervisor.py --mode demo      # control API on :8009, world on 8000-8004
python supervisor.py --mode test      # control API on :9009, world on 9000-9004
python supervisor.py --mode test --no-start   # control API only (used by the test fixtures)
```

On boot the supervisor starts the default processes **without** wiping data,
so a restarted supervisor continues the previous world. Use `/reset` for an
empty world.

The test fixtures start the test supervisor automatically if it is not running.

## Managed processes

| Name | Command (demo mode) | Started by default |
| --- | --- | --- |
| `actions` | `uvicorn services.actions:app --port 8003` | yes |
| `bridge` | `uvicorn bridge.main:app --port 8000` | yes |
| `agent_b` | `python -m adapters.agent_b --port 8002` | yes |
| `agent_a` | `python -m adapters.agent_a --port 8001` | yes |
| `agent_b2` | `python -m adapters.agent_b --id agent_b2 --port 8004` | no (tests start it) |

Start order: actions → bridge → agent_b → agent_a, each waiting for the
previous one's `/health`. Each process gets its environment from
[CONFIG.md](CONFIG.md) plus any per-start overrides such as `CRASH_AT`.
Output goes to `logs/<mode>/<name>.log`.

## Control API

| Route | Body | Does |
| --- | --- | --- |
| `GET /status` | — | Each process: `starting` (process up, `/health` not answered yet), `running`, `crashed` (exited non-zero, including a hard kill), `stopped`; pid; port; uptime; exit code; per-start `env` overrides |
| `POST /start/{name}` | `{ "env": {…} }` | Start if not running |
| `POST /stop/{name}` | — | Graceful stop (SIGTERM, wait 3 s, then kill) |
| `POST /kill/{name}` | — | **Hard kill** (SIGKILL / `TerminateProcess`); no cleanup; used for "crash" |
| `POST /restart/{name}` | `{ "env": { "CRASH_AT": "after_pay_call", "CRASH_MODE": "once" } }` | Kill, wait for the port to free, start with env |
| `POST /reset` | `{ "keep_running": true, "env": {…} }` | Stop all (agents first), delete `data/<mode>/*` (including `-wal` and `-shm` files), clear fault rules and per-process overrides, set `env` as the world environment for every start until the next reset, start all, wait for health |
| `POST /tests/run` | `{ "mode": "fast" \| "watch", "select": "s4" \| "official" \| "all" }` | Runs pytest as a subprocess; returns a `run_id` |
| `GET /tests/{run_id}` | — | Progress and results (reads `results/<run_id>/`) |
| `GET /logs/{name}?tail=200` | — | Last log lines |

CORS allows the dashboard origin (`http://localhost:8000`) so its buttons work
even while the bridge is down.

## Crash handling

- A process that exits with a non-zero code is shown as `crashed` and is
  **not** restarted automatically in demo mode. The "Recover" button restarts
  it, so the judges can see the gap.
- `AUTO_RESTART=1` (optional) restarts crashed agents after
  `AUTO_RESTART_DELAY` seconds, for the chaos run and X4. It can be set for
  one world through `/reset`'s `env`. A monitor checks every 0.1 s; only
  agents are auto-restarted, never the bridge or the actions service.
- `GET /status` counts `crashes` per process since the last reset. A `/kill` counts
  as a crash; the kill that `/restart` does itself does not. While `AUTO_RESTART` is
  on, every crashed agent with no restart pending gets one, including an agent
  that crashes again while an earlier auto-restart is still in progress.
- `CRASH_MODE=once`: `CRASH_AT` is removed from the environment on the next
  restart. `CRASH_MODE=always`: kept (retry-limit test X4).

## Watch-mode tests and the demo world

`POST /tests/run` with `mode: "watch"` runs the selected scenario against the
**demo** world (ports 8000–8004) with demo timings, after resetting it. The
scenario posts `test_step` captions through the bridge's `/annotate`.
`mode: "fast"` runs against the separate test world and never touches demo data.

## Platform notes

- **Windows:** use `subprocess.Popen(..., creationflags=CREATE_NEW_PROCESS_GROUP)`;
  `proc.kill()` maps to `TerminateProcess`. Ports can take a moment to free;
  `restart` polls the port for up to 5 s before starting again.
- **Linux/macOS:** children start with `start_new_session=True` (their own
  session and process group). `/kill` sends SIGKILL to that group with
  `os.killpg`, so it hits only that child; `/stop` sends SIGTERM, waits 3 s, then
  SIGKILL. A port counts as free once it can be bound again (`SO_REUSEADDR`),
  polled for up to 5 s. Children bind `127.0.0.1`.
- Always run the full test suite on the **actual demo laptop** before the event.
