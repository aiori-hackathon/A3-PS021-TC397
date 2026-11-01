"""C1: chaos run (docs/TESTING.md#c1--chaos-run).

50 messages from a fixed list, random fault rules on every link, random crash
points for both workers and the orchestrator, AUTO_RESTART on, two workers.
Every choice comes from random.Random(CHAOS_SEED), so the same seed injects
the same faults in the same order (timing still varies between machines).

    pytest -m slow
"""

import random
from collections import Counter

import pytest

from tests.helpers.campaign import TERMINAL, run_workload, wait_terminal

pytestmark = [
    pytest.mark.slow,
    pytest.mark.timeout(420),
    pytest.mark.world_env(AUTO_RESTART="1", AUTO_CONFIRM="1"),
]

N_MESSAGES = 50


def test_c1_chaos_run(world, settings):
    rng = random.Random(settings.chaos_seed)
    armed = run_workload(world, rng, N_MESSAGES)
    faults_set, crashes_armed = armed["faults_set"], armed["crashes_armed"]

    world.caption("Waiting until every task is terminal")
    dialogs, tasks, settled = wait_terminal(world, N_MESSAGES, timeout=300)
    if not settled:
        raise TimeoutError("C1: not every task reached a terminal state within 300 s")

    ledger = world.actions.ledger()
    per_key = Counter(row["idempotency_key"] for row in ledger)
    duplicate_effects = {k: n for k, n in per_key.items() if n > 1}
    side_effect_tasks = [t for t in tasks if t["action"]]
    completed_without_one_row = [
        t["task_id"]
        for t in side_effect_tasks
        if t["state"] == "COMPLETED" and per_key.get(f"{t['task_id']}:{t['action']}") != 1
    ]
    stuck = [t["task_id"] for t in tasks if t["state"] not in TERMINAL]
    metrics = world.bridge.get("/metrics")
    status = world.supervisor.status()
    states = Counter(t["state"] for t in tasks)
    world.record(
        seed=settings.chaos_seed,
        messages=N_MESSAGES,
        dialogs=len({d["msg_id"] for d in dialogs}),
        tasks=len(tasks),
        completed=states["COMPLETED"],
        failed=states["FAILED"],
        cancelled=states["CANCELLED"],
        fault_rules_set=sum(faults_set.values()),
        fault_rules_by_kind=dict(faults_set),
        bridge_faults_applied=metrics["faults_applied"],
        crash_points_armed=dict(crashes_armed),
        crashes=sum(v["crashes"] for v in status.values()),
        duplicate_requests_absorbed=metrics["duplicate_requests"],
        duplicate_effects_rejected=metrics["duplicate_effects_rejected"],
        stale_writes_blocked=metrics["stale_writes_blocked"],
        duplicate_side_effects=len(duplicate_effects),
        stuck_tasks=len(stuck),
        ledger_rows=len(ledger),
    )
    assert len({d["msg_id"] for d in dialogs}) == N_MESSAGES
    assert not stuck
    assert duplicate_effects == {}, f"keys performed more than once: {duplicate_effects}"
    assert completed_without_one_row == []
    assert all(n <= 1 for n in per_key.values())
