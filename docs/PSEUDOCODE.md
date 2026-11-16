# Pseudocode: the parts that keep a task from being lost or done twice

Plain-language pseudocode for the five routines that matter, taken from the
code (`bridge/handlers.py`, `bridge/leases.py`, `bridge/jobs.py`,
`adapters/agent_b.py`, `services/actions.py`). Each one marks where a crash
can land and what happens then. The tests that prove it are in the
[failure matrix](DEDUP_AND_RECOVERY.md#failure-and-recovery-matrix).

## 1. The bridge handles one state-changing request

```text
handle(request):
    BEGIN IMMEDIATE                              # one transaction holds the write lock
    stored = inbox.lookup(request.request_id)
    if stored exists:
        if stored.body_hash != hash(request):    # same ID, different request
            log event request_id_conflict        # rule REQUEST_ID_REUSED
            reply error 422
        else:
            log event duplicate_request          # rule INBOX_REPEAT
            reply stored.reply                   # nothing changes
    else:
        try:
            result = run the operation           # may move task state, only via state_machine.apply()
            reply   = success(result)
        except refused (stale fence, bad transition, not owner ...):
            log the protection event with its rule and reason
            reply   = error
        inbox.store(request.request_id, hash, reply)
    COMMIT                                       # state change + inbox reply + events, together
    after COMMIT only: push the new events to WebSocket clients
```

**Crash before COMMIT:** nothing was written; the sender retries with the same
`request_id` and the request is processed fresh. **Crash after COMMIT, before
the reply reaches the sender:** the sender retries, the inbox returns the
stored reply, nothing runs twice.

## 2. A worker claims a task (leases and fences)

```text
claim(task, worker):
    require task.state in {DISPATCHED, SUSPENDED}
    require not (task.state == SUSPENDED and task.held)    # the user said "keep paused"
    require lease is free and attempt < max_attempts
    task.owner   = worker
    task.fence  += 1                                       # every new owner gets a higher fence
    task.attempt += 1
    task.lease_until = bridge_clock_now + lease_seconds
    log event claimed / resumed / reclaimed (with the progress report)
```

Two workers claiming at the same moment: the transaction in routine 1 lets one
win; the other gets `already_claimed`. Every later write by a worker must carry
its fence; a lower fence is refused as `stale_fence`, so a slow old owner can
never overwrite the new one.

## 3. A worker performs a side effect (the effect protocol)

```text
do_effect(task):
    key = task.id + ":" + task.action                      # never includes the fence or attempt
    if task.effect.status == committed:  return task.effect.receipt     # already known
    if task.effect.status == intent:     ask the ledger (evidence)      # only for the log
    bridge.effect_intent(key)            # state -> EFFECT_PENDING, recorded BEFORE acting
    receipt = actions.perform(key)       # retried with the SAME key until an answer arrives
    bridge.effect_result(receipt)        # effect.status = committed
```

| Crash lands... | State the bridge has | What happens next |
| --- | --- | --- |
| before `effect_intent` | no intent | worker resumes, records the intent, performs it once |
| after `effect_intent`, before `perform` | intent | resume; the ledger says VERIFIED_NOT_EXECUTED; perform with the same key |
| after `perform`, before `effect_result` | intent | resume; the ledger says VERIFIED_DONE; the same key returns the **original receipt**, nothing is paid again (S4) |
| after `effect_result` | committed | resume skips the effect and carries on |

The one window that stays open is outside our control: if the **actions
service itself** performs an effect and dies before writing its ledger row, no
key was recorded. Here the ledger row and the effect are one local transaction,
so that window does not exist in this prototype; against a real third-party
service it would ([LIMITATIONS.md](LIMITATIONS.md)).

## 4. The actions service performs an effect

```text
perform(key, task_id, fence, action, params):
    BEGIN IMMEDIATE
    if fence < highest fence seen for task_id:  COMMIT; reply 409 stale_fence
    remember highest fence for task_id
    row = ledger.find(key)
    if row exists:  COMMIT; reply row.receipt with replayed = true       # no second effect
    insert ledger row (key is UNIQUE), new receipt id
    COMMIT; reply receipt with replayed = false
```

The UNIQUE key and the insert are one transaction, so two requests with the
same key at the same moment still produce one row.

## 5. A lease expires (the watchdog)

```text
every tick:
    for task in IN_PROGRESS or EFFECT_PENDING where lease_until < bridge_clock_now:
        task -> SUSPENDED                       # event lease_expired
        if attempts exhausted:  task -> FAILED  # event failed, dependants cancelled
        else (demo): Agent A holds the task and asks the user "Continue or keep paused?"
                     (unattended modes: the next claim or the owner's resume takes it)
```

Resuming never loses work: the claim reply carries a **progress report** built
from the checkpoint log (steps done, next step, effect status), so a restarted
worker, which keeps no state of its own, continues from the next step.
