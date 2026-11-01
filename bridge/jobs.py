"""Background jobs: dispatcher, lease watchdog, startup recovery.
See docs/BRIDGE.md#background-jobs.

Each job pass is one transaction; every state change goes through
state_machine.apply().
"""

from __future__ import annotations

import asyncio
import logging

from bridge import events, tasks
from bridge import state_machine as sm
from bridge.db import Database

log = logging.getLogger("bridge.jobs")

DISPATCHER = "dispatcher"
WATCHDOG = "watchdog"


def dispatch_once(db: Database) -> None:
    """CREATED / AWAITING_CONFIRMATION -> DISPATCHED when ready; cancel if a dependency failed."""
    with db.transaction() as tx:
        waiting = tx.all(
            "SELECT task_id FROM tasks WHERE state IN ('CREATED', 'AWAITING_CONFIRMATION')"
            " ORDER BY rowid"
        )
        for item in waiting:
            # Re-read: a cascade earlier in this pass may have moved it.
            task = tasks.row(tx, item["task_id"])
            target = sm.dispatcher_target(
                task["state"],
                bool(task["needs_confirmation"]),
                bool(task["confirmed"]),
                tasks.dep_states(tx, task),
            )
            if target is None:
                continue
            if target == "CANCELLED":
                tasks.cancel_task(tx, task, DISPATCHER, "dependency_failed")
            else:
                sm.apply(tx, task, target, True, actor=DISPATCHER)


def watchdog_once(db: Database, heartbeat_seconds: float) -> None:
    """Expired leases -> SUSPENDED (then FAILED at the attempt limit); silent agents -> unresponsive."""
    with db.transaction() as tx:
        expired = tx.all(
            "SELECT task_id FROM tasks WHERE state IN ('IN_PROGRESS', 'EFFECT_PENDING')"
            " AND lease_until < ? ORDER BY rowid",
            (tx.now,),
        )
        for item in expired:
            task = tasks.row(tx, item["task_id"])
            path = sm.lease_expiry_targets(task["attempt"], task["max_attempts"])
            sm.apply(
                tx,
                task,
                "SUSPENDED",
                sm.lease_expired(task["lease_until"], tx.now),
                actor=WATCHDOG,
                reason="lease_expired",
            )
            tx.event(
                "lease_expired",
                WATCHDOG,
                task["dialog_id"],
                task["task_id"],
                {
                    "owner": task["owner"],
                    "fence": task["fence"],
                    "attempt": task["attempt"],
                    "from": task["state"],
                    "effect_status": task["effect_status"],
                },
            )
            if "FAILED" in path:
                suspended = tasks.row(tx, task["task_id"])
                sm.apply(
                    tx,
                    suspended,
                    "FAILED",
                    sm.retries_exhausted(suspended["attempt"], suspended["max_attempts"]),
                    actor=WATCHDOG,
                    reason="max_attempts",
                    changes={"reason": "max_attempts"},
                )
                data = {"attempt": suspended["attempt"], "max_attempts": suspended["max_attempts"]}
                tx.event("max_attempts_reached", WATCHDOG, task["dialog_id"], task["task_id"], data)
                tx.event(
                    "failed",
                    WATCHDOG,
                    task["dialog_id"],
                    task["task_id"],
                    {"reason": "max_attempts"},
                )
                tasks.cancel_dependents(tx, suspended, WATCHDOG)

        silent = tx.all(
            "SELECT agent_id, last_heartbeat FROM agents WHERE status = 'online'"
            " AND last_heartbeat < ?",
            (tx.now - 2 * heartbeat_seconds,),
        )
        for agent in silent:
            tx.execute(
                "UPDATE agents SET status = 'unresponsive' WHERE agent_id = ?", (agent["agent_id"],)
            )
            tx.event(
                "agent_unresponsive",
                WATCHDOG,
                data={
                    "agent_id": agent["agent_id"],
                    "silent_for_s": round(tx.now - agent["last_heartbeat"], 2),
                },
            )


def startup_recovery(db: Database, boot_id: str, startup_grace: float) -> int:
    """Once, before serving: re-arm in-flight leases with a grace period, mark agents unknown,
    and log bridge_started with what was recovered. Returns the bridge_started seq."""
    with db.transaction() as tx:
        previous = events.last_seq(tx)
        in_flight = tx.execute(
            "UPDATE tasks SET lease_until = ? WHERE state IN ('IN_PROGRESS', 'EFFECT_PENDING')",
            (tx.now + startup_grace,),
        ).rowcount
        tx.execute("UPDATE agents SET status = 'unknown'")
        open_tasks = tx.one(
            "SELECT COUNT(*) FROM tasks WHERE state NOT IN ('COMPLETED', 'FAILED', 'CANCELLED')"
        )[0]
        open_dialogs = tx.one(
            "SELECT COUNT(DISTINCT dialog_id) FROM tasks"
            " WHERE state NOT IN ('COMPLETED', 'FAILED', 'CANCELLED')"
        )[0]
        return tx.event(
            "bridge_started",
            "bridge",
            data={
                "boot_id": boot_id,
                "previous_last_seq": previous,
                "recovered_dialogs": open_dialogs,
                "recovered_tasks": open_tasks,
                "in_flight_tasks": in_flight,
                "startup_grace_s": startup_grace,
            },
        )


async def every(name: str, tick: float, job, *args) -> None:
    """Run a blocking job off the event loop every `tick` seconds, forever."""
    while True:
        try:
            await asyncio.to_thread(job, *args)
        except Exception:  # keep the loop alive; the error is in the log
            log.exception("%s failed", name)
        await asyncio.sleep(tick)
