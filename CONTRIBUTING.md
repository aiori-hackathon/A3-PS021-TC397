# Contributing (team workflow)

## Branches

- `main` always works: `TEST_MODE=fast pytest` passes on it.
- One branch per piece of work: `feat/inbox`, `feat/agent-b-resume`, `test/s4`, `fix/lease-expiry`.
- Open a pull request into `main`; the other teammate reviews it, even briefly.
- Small PRs (under ~300 lines) get reviewed properly; big ones don't.

## Commits

Format: `area: what changed`, present tense.

```
bridge: add atomic claim with fence increment
adapters: retry effect with same key after timeout
tests: add S4 crash-after-effect scenario
docs: map SUSPENDED to A2A-style metadata
```

## Before every push

```bash
TEST_MODE=fast pytest            # must pass
```

## Never commit

- `data/`, `logs/`, `results/` (generated)
- `.venv/`, `__pycache__/`
- Any `.env` file, token, password or API key

## Changing the contract

[docs/SCHEMA.md](docs/SCHEMA.md), [docs/IDENTIFIERS.md](docs/IDENTIFIERS.md)
and [docs/STATE_MACHINE.md](docs/STATE_MACHINE.md) are the contract between
the core and the edges. Change them only in a PR that both teammates approve,
and update the docs in the same PR as the code.

## Code style

- Python 3.11+, type hints on every function.
- Format with `ruff format`, lint with `ruff check`.
- Pure functions where possible (router, codecs, state machine guards) so they
  can be unit-tested without servers.
- No `time.sleep` in async code; no fixed sleeps in tests.
