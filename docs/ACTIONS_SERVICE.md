# Mock Actions Service

A tiny FastAPI service (port 8003, test 9003) that stands in for the real world:
a payment gateway, a mail server, a booking system. It exists so side effects
are **real enough to count** and **impossible to repeat by key**.

## API

| Route | Purpose |
| --- | --- |
| `POST /actions` | Perform a side effect (idempotent by key) |
| `GET /ledger?task_id=&action=` | All performed effects, filterable |
| `GET /ledger/count?task_id=&action=` | `{ "count": 1 }`, used by tests |
| `GET /receipts/{receipt_id}` | One receipt, used by the `verify` step |
| `GET /health` | Liveness |
| `POST /faults`, `DELETE /faults` | Fault rules for this service ([FAULT_INJECTION.md](FAULT_INJECTION.md)) |

`POST /actions` request:

```json
{
  "idempotency_key": "tsk_8c1f0a2b9e44:pay",
  "task_id": "tsk_8c1f0a2b9e44",
  "fence": 1,
  "action": "pay",
  "params": { "amount": 40, "currency": "INR", "payee": "bakery" }
}
```

Reply:

```json
{ "receipt_id": "rcp_51d0a8c2e911", "replayed": false, "action": "pay", "performed_at": "2026-11-02T10:15:04.002Z" }
```

A second call with the same key returns the **same** `receipt_id` with
`"replayed": true`, and no new row is written.

## Ledger

`data/<mode>/ledger.db`, WAL mode, `synchronous = FULL`.

```sql
CREATE TABLE ledger (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  idempotency_key TEXT NOT NULL,
  receipt_id      TEXT NOT NULL UNIQUE,
  task_id         TEXT NOT NULL,
  action          TEXT NOT NULL,      -- pay | send | reserve
  params          TEXT NOT NULL,
  fence           INTEGER NOT NULL,
  performed_at    TEXT NOT NULL
);

CREATE UNIQUE INDEX ledger_key ON ledger(idempotency_key);  -- skipped when DISABLE_DEDUP=1

CREATE TABLE fences (
  task_id   TEXT PRIMARY KEY,
  max_fence INTEGER NOT NULL
);
```

## Handling a request

```python
with ledger.transaction():                          # BEGIN IMMEDIATE
    if fence < max_fence_for(task_id):
        return 409, {"error": {"code": "stale_fence"}}
    raise_max_fence(task_id, fence)
    row = find(idempotency_key)
    if row and not DISABLE_DEDUP:
        return 200, {**row, "replayed": True}
    receipt = insert(idempotency_key, ...)          # the "side effect"
    return 200, {**receipt, "replayed": False}
```

With `DISABLE_DEDUP=1` the key lookup is skipped and the unique index is not
created (the database is wiped on every reset, so this only ever applies to a
fresh test or demo world). Duplicates then show up as extra rows. This is how the dashboard shows "2
payments" with protection off.

## Actions

All three are mock effects that only write a ledger row:

| Action | Params | Receipt adds |
| --- | --- | --- |
| `pay` | `amount`, `currency`, `payee` | `amount`, `payee` |
| `send` | `to`, `body` | `to` |
| `reserve` | `what`, `day`, `time`, `ref` | `confirmation_code` |
