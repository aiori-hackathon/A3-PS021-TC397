"""Counters and timings computed from events and tables. See docs/BRIDGE.md#metrics."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from bridge.db import Tx

COUNTERS = {
    "duplicate_requests": "duplicate_request",
    "duplicate_effects_rejected": "duplicate_rejected",
    "stale_writes_blocked": "stale_fence_blocked",
    "invalid_transitions_rejected": "invalid_transition_rejected",
    "schema_rejections": "schema_rejected",
    "lease_expiries": "lease_expired",
    "recoveries": "resumed",
    "reclaims": "reclaimed",
    "faults_applied": "fault_applied",
}
TIMELINE_TYPES = ("claimed", "completed", "lease_expired", "resumed", "reclaimed")
INTERRUPTIONS = {"lease_expired", "resumed", "reclaimed"}


def _ms(start: str, end: str) -> float:
    return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() * 1000


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def compute(tx: Tx) -> dict[str, Any]:
    by_type = {r[0]: r[1] for r in tx.all("SELECT type, COUNT(*) FROM events GROUP BY type")}
    by_state = {r[0]: r[1] for r in tx.all("SELECT state, COUNT(*) FROM tasks GROUP BY state")}
    out: dict[str, Any] = {name: by_type.get(t, 0) for name, t in COUNTERS.items()}
    out["tasks_completed"] = by_state.get("COMPLETED", 0)
    out["tasks_failed"] = by_state.get("FAILED", 0)
    out["tasks_cancelled"] = by_state.get("CANCELLED", 0)
    out["tasks_total"] = sum(by_state.values())

    marks = ",".join("?" * len(TIMELINE_TYPES))
    timeline: dict[str, list[tuple[str, str]]] = {}
    for r in tx.all(
        f"SELECT task_id, type, ts FROM events WHERE type IN ({marks}) ORDER BY seq",
        TIMELINE_TYPES,
    ):
        timeline.setdefault(r["task_id"], []).append((r["type"], r["ts"]))

    task_ms, recovery_ms = [], []
    for items in timeline.values():
        types = [t for t, _ in items]
        if "claimed" in types and "completed" in types and not INTERRUPTIONS & set(types):
            claimed = next(ts for t, ts in items if t == "claimed")
            completed = next(ts for t, ts in items if t == "completed")
            task_ms.append(_ms(claimed, completed))
        for i, (t, ts) in enumerate(items):
            if t == "lease_expired":
                back = next(
                    (ts2 for t2, ts2 in items[i + 1 :] if t2 in ("resumed", "reclaimed")), None
                )
                if back:
                    recovery_ms.append(_ms(ts, back))
    out["avg_task_ms"] = _avg(task_ms)
    out["avg_recovery_ms"] = _avg(recovery_ms)
    return json.loads(json.dumps(out))
