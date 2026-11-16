"""Replay check: the event log alone must explain the database.

`rebuild` walks the event log and works out, for every task, its state, fence,
attempt, effect status and hold. `compare` sets that against the `tasks` table.
Agreement is evidence that nothing changed task state without leaving an event
(the log is append-only, see bridge/db.py), and the hash of the rebuilt state is
the same on every run over the same log, so repeating the check proves nothing
moved. See docs/BRIDGE.md#replay-check.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from bridge.db import Tx
from bridge.events import row_to_event

FIELDS = ("state", "fence", "attempt", "effect", "held")


def rebuild(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    tasks: dict[str, dict[str, Any]] = {}
    for e in events:
        task_id, kind, d = e.get("task_id"), e["type"], e.get("data") or {}
        if not task_id:
            continue
        if kind == "task_created":
            tasks[task_id] = {
                "state": "CREATED",
                "fence": 0,
                "attempt": 0,
                "effect": "none",
                "held": False,
            }
            continue
        t = tasks.get(task_id)
        if t is None:
            continue
        if kind == "state_changed":
            t["state"] = d["to"]
        elif kind in ("claimed", "resumed", "reclaimed"):
            t["fence"], t["attempt"] = d["fence"], d["attempt"]
        elif kind == "effect_intent":
            t["effect"] = "intent"
        elif kind == "effect_committed":
            t["effect"] = "committed"
        elif kind == "held":
            t["held"] = True
        elif kind == "released":
            t["held"] = False
    for t in tasks.values():
        t["held"] = bool(t["held"] and t["state"] == "SUSPENDED")  # as the task record shows it
    return tasks


def compare(rebuilt: dict[str, dict[str, Any]], rows: list[Any]) -> list[dict[str, Any]]:
    """Every difference between the rebuilt tasks and the `tasks` rows."""
    out: list[dict[str, Any]] = []
    seen = set()
    for r in rows:
        task_id = r["task_id"]
        seen.add(task_id)
        t = rebuilt.get(task_id)
        if t is None:
            out.append(
                {"task_id": task_id, "field": "task", "database": "present", "from_log": None}
            )
            continue
        db = {
            "state": r["state"],
            "fence": r["fence"],
            "attempt": r["attempt"],
            "effect": r["effect_status"],
            "held": bool(r["held"]) and r["state"] == "SUSPENDED",
        }
        for field in FIELDS:
            if db[field] != t[field]:
                out.append(
                    {
                        "task_id": task_id,
                        "field": field,
                        "database": db[field],
                        "from_log": t[field],
                    }
                )
    for task_id in rebuilt:
        if task_id not in seen:
            out.append(
                {"task_id": task_id, "field": "task", "database": None, "from_log": "present"}
            )
    return out


def state_hash(rebuilt: dict[str, dict[str, Any]]) -> str:
    canonical = json.dumps(rebuilt, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def verify(tx: Tx) -> dict[str, Any]:
    events = [row_to_event(r) for r in tx.all("SELECT * FROM events ORDER BY seq")]
    seqs = [e["seq"] for e in events]
    rebuilt = rebuild(events)
    mismatches = compare(rebuilt, tx.all("SELECT * FROM tasks ORDER BY rowid"))
    contiguous = seqs == list(range(1, len(seqs) + 1))
    return {
        "ok": contiguous and not mismatches,
        "events": len(events),
        "seq_contiguous": contiguous,
        "tasks_rebuilt": len(rebuilt),
        "mismatches": mismatches,
        "state_hash": state_hash(rebuilt),
    }
