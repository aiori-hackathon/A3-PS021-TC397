"""Background jobs: dispatcher (Phase 2), lease watchdog and startup recovery
(Phase 3). See docs/BRIDGE.md#background-jobs.

Each job pass is one transaction; every state change goes through
state_machine.apply().
"""

from __future__ import annotations

import asyncio
import logging

from bridge import state_machine as sm
from bridge import tasks
from bridge.db import Database

log = logging.getLogger("bridge.jobs")

DISPATCHER = "dispatcher"


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


async def every(name: str, tick: float, job, *args) -> None:
    """Run a blocking job off the event loop every `tick` seconds, forever."""
    while True:
        try:
            await asyncio.to_thread(job, *args)
        except Exception:  # keep the loop alive; the error is in the log
            log.exception("%s failed", name)
        await asyncio.sleep(tick)
