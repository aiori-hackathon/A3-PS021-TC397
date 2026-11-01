"""Dialog and task operations that are not claims or leases: create, confirm,
cancel, checkpoint, effect intent/result, complete, fail. Each runs inside the
request's transaction; every state change goes through state_machine.apply().
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from bridge import state_machine as sm
from bridge.db import Tx, dumps, iso
from bridge.errors import BridgeError
from bridge.leases import check_writer
from bridge.models import (
    CancelPayload,
    CheckpointPayload,
    CompletePayload,
    DialogCreatePayload,
    EffectIntentPayload,
    EffectResultPayload,
    Envelope,
    FailPayload,
    Task,
)

PRE_RUN = ("CREATED", "AWAITING_CONFIRMATION", "DISPATCHED")


def new_id(prefix: str) -> str:
    """dialog_id / task_id: prefix + 12 hex chars (docs/IDENTIFIERS.md#formats)."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# --- reading -------------------------------------------------------------------


def record(tx: Tx, row: sqlite3.Row) -> dict[str, Any]:
    """A tasks row as the shared-schema Task record."""
    cp = tx.one(
        "SELECT step_index, step, output FROM checkpoints WHERE task_id = ?"
        " ORDER BY step_index DESC LIMIT 1",
        (row["task_id"],),
    )
    task = {
        "task_id": row["task_id"],
        "dialog_id": row["dialog_id"],
        "index": row["idx"],
        "intent": row["intent"],
        "action": row["action"],
        "params": json.loads(row["params"]),
        "steps": json.loads(row["steps"]),
        "state": row["state"],
        "owner": row["owner"],
        "lease_until": iso(row["lease_until"]) if row["lease_until"] is not None else None,
        "fence": row["fence"],
        "attempt": row["attempt"],
        "max_attempts": row["max_attempts"],
        "depends_on": json.loads(row["depends_on"]),
        "needs_confirmation": bool(row["needs_confirmation"]),
        "confirmed": bool(row["confirmed"]),
        "effect": {
            "key": row["effect_key"],
            "status": row["effect_status"],
            "receipt_id": row["receipt_id"],
            "replayed": bool(row["effect_replayed"]),
        },
        "last_checkpoint": (
            {"step_index": cp["step_index"], "step": cp["step"], "output": json.loads(cp["output"])}
            if cp
            else None
        ),
        "result": json.loads(row["result"]) if row["result"] is not None else None,
        "reason": row["reason"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    return Task.model_validate(task).model_dump(mode="json")


def row(tx: Tx, task_id: str) -> sqlite3.Row | None:
    return tx.one("SELECT * FROM tasks WHERE task_id = ?", (task_id,))


def load(tx: Tx, env: Envelope) -> sqlite3.Row:
    if not env.task_id:
        raise BridgeError("validation_error", f"{env.op} needs a task_id")
    found = row(tx, env.task_id)
    if found is None:
        raise BridgeError("not_found", f"unknown task {env.task_id}", {"task_id": env.task_id})
    if env.dialog_id is not None and env.dialog_id != found["dialog_id"]:
        raise BridgeError(
            "validation_error",
            f"task {env.task_id} belongs to {found['dialog_id']}, not {env.dialog_id}",
        )
    return found


def reload(tx: Tx, task_id: str) -> dict[str, Any]:
    return record(tx, row(tx, task_id))


def dialog_record(tx: Tx, dialog_row: sqlite3.Row) -> dict[str, Any]:
    ids = [
        r["task_id"]
        for r in tx.all(
            "SELECT task_id FROM tasks WHERE dialog_id = ? ORDER BY idx", (dialog_row["dialog_id"],)
        )
    ]
    return {
        "dialog_id": dialog_row["dialog_id"],
        "msg_id": dialog_row["msg_id"],
        "text": dialog_row["text"],
        "created_by": dialog_row["created_by"],
        "task_ids": ids,
        "created_at": dialog_row["created_at"],
    }


def dialog_view(tx: Tx, dialog_id: str) -> dict[str, Any] | None:
    found = tx.one("SELECT * FROM dialogs WHERE dialog_id = ?", (dialog_id,))
    if found is None:
        return None
    rows = tx.all("SELECT * FROM tasks WHERE dialog_id = ? ORDER BY idx", (dialog_id,))
    return {"dialog": dialog_record(tx, found), "tasks": [record(tx, r) for r in rows]}


def dep_states(tx: Tx, task: sqlite3.Row) -> list[str]:
    deps = json.loads(task["depends_on"])
    if not deps:
        return []
    marks = ",".join("?" * len(deps))
    rows = tx.all(
        f"SELECT state FROM tasks WHERE dialog_id = ? AND idx IN ({marks})",
        (task["dialog_id"], *deps),
    )
    return [r["state"] for r in rows]


# --- dialog.create / task.confirm / task.cancel --------------------------------------


def create_dialog(
    tx: Tx, env: Envelope, max_attempts: int, disable_dedup: bool = False
) -> dict[str, Any]:
    p = DialogCreatePayload.model_validate(env.payload)
    taken = tx.one("SELECT dialog_id FROM dialogs WHERE msg_id = ?", (p.msg_id,))
    if taken and not disable_dedup:
        raise BridgeError(
            "validation_error",
            f"msg_id {p.msg_id} already started dialog {taken['dialog_id']}",
            {"dialog_id": taken["dialog_id"]},
        )
    dialog_id = new_id("dlg")
    tx.execute(
        "INSERT INTO dialogs (dialog_id, msg_id, text, created_by, created_at) VALUES (?,?,?,?,?)",
        (dialog_id, p.msg_id, p.text, env.sender, tx.ts),
    )
    task_ids = [new_id("tsk") for _ in p.tasks]
    tx.event(
        "dialog_created",
        env.sender,
        dialog_id,
        data={"msg_id": p.msg_id, "text": p.text, "task_ids": task_ids},
    )
    for index, (task_id, spec) in enumerate(zip(task_ids, p.tasks, strict=True), start=1):
        tx.execute(
            "INSERT INTO tasks (task_id, dialog_id, idx, intent, action, params, steps, state,"
            " max_attempts, depends_on, needs_confirmation, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?, 'CREATED', ?,?,?,?,?)",
            (
                task_id,
                dialog_id,
                index,
                spec.intent,
                spec.action,
                dumps(spec.params),
                dumps(spec.steps),
                max_attempts,
                dumps(spec.depends_on),
                int(spec.needs_confirmation),
                tx.ts,
                tx.ts,
            ),
        )
        tx.event(
            "task_created",
            env.sender,
            dialog_id,
            task_id,
            {
                "index": index,
                "intent": spec.intent,
                "action": spec.action,
                "params": spec.params,
                "depends_on": spec.depends_on,
                "needs_confirmation": spec.needs_confirmation,
            },
        )
    view = dialog_view(tx, dialog_id)
    return {"dialog_id": dialog_id, "task_ids": task_ids, **view}


def confirm(tx: Tx, env: Envelope) -> dict[str, Any]:
    task = load(tx, env)
    if task["state"] in sm.TERMINAL:
        raise sm.InvalidTransition(task["state"], "DISPATCHED", "task is already finished")
    if not task["confirmed"]:
        tx.execute(
            "UPDATE tasks SET confirmed = 1, updated_at = ? WHERE task_id = ?",
            (tx.ts, task["task_id"]),
        )
        tx.event("confirmed", env.sender, task["dialog_id"], task["task_id"])
    return reload(tx, task["task_id"])


def cancel_task(tx: Tx, task: sqlite3.Row, actor: str, reason: str) -> None:
    sm.apply(
        tx,
        task,
        "CANCELLED",
        sm.can_cancel(task["state"], task["effect_status"]),
        actor=actor,
        reason=reason,
        changes={"reason": reason, "lease_until": None},
    )
    tx.event("cancelled", actor, task["dialog_id"], task["task_id"], {"reason": reason})
    cancel_dependents(tx, task, actor)


def cancel_dependents(tx: Tx, task: sqlite3.Row, actor: str) -> None:
    """Tasks waiting on a failed or cancelled task can never run: cancel them too."""
    marks = ",".join("?" * len(PRE_RUN))
    waiting = tx.all(
        f"SELECT task_id, depends_on FROM tasks WHERE dialog_id = ? AND state IN ({marks})",
        (task["dialog_id"], *PRE_RUN),
    )
    for dep in waiting:
        if task["idx"] not in json.loads(dep["depends_on"]):
            continue
        current = row(tx, dep["task_id"])  # an earlier cascade may have moved it
        if current["state"] in PRE_RUN:
            cancel_task(tx, current, actor, "dependency_failed")


def cancel(tx: Tx, env: Envelope) -> dict[str, Any]:
    task = load(tx, env)
    cancel_task(tx, task, env.sender, CancelPayload.model_validate(env.payload).reason)
    return reload(tx, task["task_id"])


# --- worker writes -------------------------------------------------------------------


def _require_working(task: sqlite3.Row, what: str) -> None:
    if not sm.can_checkpoint(task["state"]):
        raise BridgeError(
            "invalid_transition",
            f"cannot {what} a {task['state']} task",
            {"from": task["state"], "to": task["state"]},
        )


def checkpoint(tx: Tx, env: Envelope) -> dict[str, Any]:
    task = load(tx, env)
    check_writer(task, env)
    _require_working(task, "checkpoint")
    p = CheckpointPayload.model_validate(env.payload)
    steps = json.loads(task["steps"])
    if p.step_index >= len(steps) or steps[p.step_index] != p.step:
        raise BridgeError(
            "validation_error",
            f"step {p.step_index} of this task is not {p.step!r}",
            {"steps": steps},
        )
    existing = tx.one(
        "SELECT fence FROM checkpoints WHERE task_id = ? AND step_index = ?",
        (task["task_id"], p.step_index),
    )
    # A step is only ever overwritten by a newer owner (higher or equal fence).
    tx.execute(
        "INSERT INTO checkpoints (task_id, step_index, step, output, fence, created_at)"
        " VALUES (?,?,?,?,?,?)"
        " ON CONFLICT(task_id, step_index) DO UPDATE SET step = excluded.step,"
        " output = excluded.output, fence = excluded.fence, created_at = excluded.created_at"
        " WHERE excluded.fence >= checkpoints.fence",
        (task["task_id"], p.step_index, p.step, dumps(p.output), env.fence, tx.ts),
    )
    tx.execute("UPDATE tasks SET updated_at = ? WHERE task_id = ?", (tx.ts, task["task_id"]))
    tx.event(
        "checkpoint",
        env.sender,
        task["dialog_id"],
        task["task_id"],
        {
            "step_index": p.step_index,
            "step": p.step,
            "fence": env.fence,
            "replaced": existing is not None,
        },
    )
    return reload(tx, task["task_id"])


def effect_intent(tx: Tx, env: Envelope) -> dict[str, Any]:
    task = load(tx, env)
    check_writer(task, env)
    p = EffectIntentPayload.model_validate(env.payload)
    if task["action"] is None or p.action != task["action"]:
        raise BridgeError(
            "validation_error",
            f"task action is {task['action']!r}, not {p.action!r}",
        )
    expected = sm.expected_effect_key(task["task_id"], task["action"])
    if p.effect_key != expected:
        raise BridgeError(
            "validation_error",
            f"effect_key must be {expected}",
            {"expected": expected, "got": p.effect_key},
        )
    sm.apply(
        tx,
        task,
        "EFFECT_PENDING",
        sm.can_record_intent(task["effect_status"], task["effect_key"], p.effect_key),
        actor=env.sender,
        changes={"effect_key": p.effect_key, "effect_status": "intent"},
    )
    tx.event(
        "effect_intent",
        env.sender,
        task["dialog_id"],
        task["task_id"],
        {
            "effect_key": p.effect_key,
            "fence": env.fence,
            "repeat": task["effect_status"] == "intent",
        },
    )
    return reload(tx, task["task_id"])


def effect_result(tx: Tx, env: Envelope) -> dict[str, Any]:
    task = load(tx, env)
    check_writer(task, env)
    _require_working(task, "record an effect result for")
    if task["effect_status"] != "intent":
        raise BridgeError(
            "invalid_transition",
            f"effect status is {task['effect_status']!r}; an intent must be recorded first",
            {"from": task["state"], "to": task["state"], "effect_status": task["effect_status"]},
        )
    p = EffectResultPayload.model_validate(env.payload)
    tx.execute(
        "UPDATE tasks SET effect_status = 'committed', receipt_id = ?, effect_replayed = ?,"
        " updated_at = ? WHERE task_id = ?",
        (p.receipt_id, int(p.replayed), tx.ts, task["task_id"]),
    )
    data = {
        "effect_key": task["effect_key"],
        "receipt_id": p.receipt_id,
        "replayed": p.replayed,
        "fence": env.fence,
    }
    tx.event("effect_committed", env.sender, task["dialog_id"], task["task_id"], data)
    if p.replayed:
        # The actions service recognised the key and returned the original receipt.
        tx.event("duplicate_rejected", env.sender, task["dialog_id"], task["task_id"], data)
    return reload(tx, task["task_id"])


def complete(tx: Tx, env: Envelope) -> dict[str, Any]:
    task = load(tx, env)
    check_writer(task, env)
    p = CompletePayload.model_validate(env.payload)
    sm.apply(
        tx,
        task,
        "COMPLETED",
        sm.can_complete(task["state"], task["action"], task["effect_status"]),
        actor=env.sender,
        changes={
            "result": dumps(p.result) if p.result is not None else None,
            "lease_until": None,
        },
    )
    tx.event(
        "completed",
        env.sender,
        task["dialog_id"],
        task["task_id"],
        {"fence": task["fence"], "attempt": task["attempt"]},
    )
    return reload(tx, task["task_id"])


def fail(tx: Tx, env: Envelope) -> dict[str, Any]:
    task = load(tx, env)
    check_writer(task, env)
    p = FailPayload.model_validate(env.payload)
    target = sm.fail_target(p.retryable)
    sm.apply(
        tx,
        task,
        target,
        True,
        actor=env.sender,
        reason=p.reason,
        changes={"reason": p.reason, "lease_until": None},
    )
    if target == "FAILED":
        tx.event("failed", env.sender, task["dialog_id"], task["task_id"], {"reason": p.reason})
        cancel_dependents(tx, task, env.sender)
    return reload(tx, task["task_id"])
