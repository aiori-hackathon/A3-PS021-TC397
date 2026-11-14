"""The progress report a worker gets when it claims or resumes a task.

Built from the bridge's own checkpoint log, so a restarted worker (which keeps no
state of its own) learns exactly which steps are done and where to continue.
See docs/BRIDGE.md#progress-report.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from bridge import state_machine as sm
from bridge.db import Tx


def report(tx: Tx, task: sqlite3.Row) -> dict[str, Any]:
    steps = json.loads(task["steps"])
    rows = tx.all(
        "SELECT step_index, step, output FROM checkpoints WHERE task_id = ? ORDER BY step_index",
        (task["task_id"],),
    )
    done = [
        {"step_index": r["step_index"], "step": r["step"], "output": json.loads(r["output"])}
        for r in rows
    ]
    nxt = done[-1]["step_index"] + 1 if done else 0
    return {
        "steps_total": len(steps),
        "done": done,
        "next_step_index": nxt,
        "next_step": steps[nxt] if nxt < len(steps) else None,
        "effect": task["effect_status"],
        "summary": sm.progress_summary(steps, [d["step"] for d in done]),
    }
