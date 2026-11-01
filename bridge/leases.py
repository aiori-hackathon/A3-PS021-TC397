"""Claim, resume, heartbeat and fence checks, timed on the bridge clock only.
See docs/BRIDGE.md#leases-and-fences.

A claim is read, guard-checked and written inside the request's
BEGIN IMMEDIATE transaction, which holds SQLite's write lock (plus the
bridge's connection lock) for the whole time, so two simultaneous claims
cannot both see the task as free. The state write itself is compare-and-set
in state_machine.apply().
"""

from __future__ import annotations

import sqlite3
from typing import Any

from bridge import state_machine as sm
from bridge.db import Tx, iso
from bridge.errors import BridgeError
from bridge.models import Envelope, HeartbeatPayload


def check_writer(task: sqlite3.Row, env: Envelope) -> None:
    """Every worker write carries its fence. Lower than current -> stale_fence."""
    if env.fence is None:
        raise BridgeError("validation_error", f"{env.op} must carry the sender's fence")
    if env.fence < task["fence"]:
        raise BridgeError(
            "stale_fence",
            f"fence {env.fence} < current {task['fence']}",
            {"current_fence": task["fence"]},
        )
    if task["owner"] != env.sender or env.fence != task["fence"]:
        raise BridgeError(
            "not_owner",
            f"{env.sender} with fence {env.fence} does not hold {task['task_id']}",
            {"owner": task["owner"], "current_fence": task["fence"]},
        )
    if task["state"] == "SUSPENDED":
        raise BridgeError(
            "not_owner",
            "the lease expired; resume the task or wait for a reclaim",
            {"owner": task["owner"], "state": "SUSPENDED"},
        )


def _take(tx: Tx, task: sqlite3.Row, agent: str, lease_seconds: float, guard_ok: bool) -> None:
    sm.apply(
        tx,
        task,
        "IN_PROGRESS",
        guard_ok,
        actor=agent,
        changes={
            "owner": agent,
            "lease_until": tx.now + lease_seconds,
            "fence": task["fence"] + 1,
            "attempt": task["attempt"] + 1,
        },
    )


def claim_row(tx: Tx, task: sqlite3.Row, agent: str, lease_seconds: float) -> None:
    state = task["state"]
    if state in sm.WORKING:
        raise BridgeError(
            "already_claimed",
            f"{task['task_id']} is held by {task['owner']}",
            {
                "owner": task["owner"],
                "lease_until": iso(task["lease_until"]) if task["lease_until"] else None,
            },
        )
    if state not in ("DISPATCHED", "SUSPENDED"):
        raise sm.InvalidTransition(state, "IN_PROGRESS", f"a {state} task cannot be claimed")
    guard = sm.lease_free(task["lease_until"], tx.now)
    if state == "SUSPENDED":
        guard = guard and sm.can_resume(task["attempt"], task["max_attempts"])
    _take(tx, task, agent, lease_seconds, guard)
    if state == "DISPATCHED":
        kind = "claimed"
    else:
        kind = "resumed" if task["owner"] == agent else "reclaimed"
    tx.event(
        kind,
        agent,
        task["dialog_id"],
        task["task_id"],
        {
            "fence": task["fence"] + 1,
            "attempt": task["attempt"] + 1,
            "previous_owner": task["owner"],
        },
    )


def claim(tx: Tx, task: sqlite3.Row, env: Envelope, lease_seconds: float) -> None:
    claim_row(tx, task, env.sender, lease_seconds)


def next_claimable(tx: Tx) -> sqlite3.Row | None:
    return tx.one(
        "SELECT * FROM tasks WHERE state IN ('DISPATCHED', 'SUSPENDED')"
        " AND attempt < max_attempts AND (lease_until IS NULL OR lease_until < ?)"
        " ORDER BY rowid LIMIT 1",
        (tx.now,),
    )


def resume(tx: Tx, env: Envelope, lease_seconds: float) -> list[str]:
    """Re-claim every non-terminal task this agent owned, each with a new fence."""
    owned = tx.all(
        "SELECT * FROM tasks WHERE owner = ?"
        " AND state IN ('IN_PROGRESS', 'EFFECT_PENDING', 'SUSPENDED') ORDER BY rowid",
        (env.sender,),
    )
    resumed = []
    for task in owned:
        if not sm.can_resume(task["attempt"], task["max_attempts"]):
            continue  # out of attempts; the watchdog fails it when the lease runs out
        if task["state"] == "SUSPENDED" and not sm.lease_free(task["lease_until"], tx.now):
            continue
        _take(tx, task, env.sender, lease_seconds, True)
        tx.event(
            "resumed",
            env.sender,
            task["dialog_id"],
            task["task_id"],
            {"fence": task["fence"] + 1, "attempt": task["attempt"] + 1, "from": task["state"]},
        )
        resumed.append(task["task_id"])
    return resumed


def heartbeat(tx: Tx, env: Envelope, lease_seconds: float) -> dict[str, str]:
    """Extend the lease of each listed task the sender currently owns."""
    p = HeartbeatPayload.model_validate(env.payload)
    leases: dict[str, Any] = {}
    until = tx.now + lease_seconds
    for task_id in p.task_ids:
        cur = tx.execute(
            "UPDATE tasks SET lease_until = ? WHERE task_id = ? AND owner = ?"
            " AND state IN ('IN_PROGRESS', 'EFFECT_PENDING')",
            (until, task_id, env.sender),
        )
        if cur.rowcount == 1:
            leases[task_id] = iso(until)
    return leases
