"""C1: chaos run (docs/TESTING.md#c1--chaos-run).

50 messages from a fixed list, random fault rules on every link, random crash
points for both workers and the orchestrator, AUTO_RESTART on, two workers.
Every choice comes from random.Random(CHAOS_SEED), so the same seed injects
the same faults in the same order (timing still varies between machines).

    pytest -m slow
"""

import random
from collections import Counter

import httpx
import pytest

from tests.helpers.wait import wait_until

pytestmark = [
    pytest.mark.slow,
    pytest.mark.timeout(420),
    pytest.mark.world_env(AUTO_RESTART="1", AUTO_CONFIRM="1"),
]

N_MESSAGES = 50
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}
MESSAGES = [
    "Pay ₹40 to the bakery and email the receipt to Rahul",
    "pay ₹120 to the landlord",
    "book a room for friday 5pm",
    "find booking HK-204",
    "send money to Asha",
    "email the report to the team",
    "reserve a table for tomorrow 7:30 pm then notify Rahul",
    "check my payment history",
    "write me a poem",
    "buy two tickets and email the tickets to Asha",
]
WORKER_CRASHES = [
    "before_pay",
    "after_pay",
    "after_pay_call",
    "before_send",
    "after_send_call",
    "after_send",
    "before_reserve",
    "after_reserve_call",
    "after_parse",
    "before_verify",
    "before_report",
]
ORCHESTRATOR_CRASHES = ["before_create", "after_create"]
WORKER_OPS = ["task.checkpoint", "task.effect_intent", "task.effect_result", "task.complete"]


def random_fault(rng: random.Random) -> tuple[str, dict]:
    kind = rng.randrange(8)
    if kind == 0:
        return "bridge", {
            "link": "agent_a",
            "action": "drop_reply",
            "match": {"op": "dialog.create"},
        }
    if kind == 1:
        return "bridge", {"link": "agent_a", "action": "drop_request", "count": rng.randint(1, 3)}
    if kind == 2:
        link = rng.choice(["agent_b", "agent_b2"])
        return "bridge", {"link": link, "action": "drop_reply", "count": rng.randint(1, 3)}
    if kind == 3:
        link = rng.choice(["agent_b", "agent_b2"])
        return "bridge", {"link": link, "action": "drop_request", "count": rng.randint(1, 3)}
    if kind == 4:
        link = rng.choice(["agent_b", "agent_b2"])
        return "bridge", {
            "link": link,
            "action": "duplicate",
            "match": {"op": rng.choice(WORKER_OPS)},
        }
    if kind == 5:
        link = rng.choice(["agent_b", "agent_b2"])
        return "bridge", {"link": link, "action": "delay", "delay_ms": 300, "count": 3}
    if kind == 6:
        action = rng.choice(["pay", "send", "reserve"])
        return "actions", {"link": "actions", "action": "drop_reply", "match": {"action": action}}
    return "actions", {"link": "actions", "action": "drop_request", "count": rng.randint(1, 2)}


def test_c1_chaos_run(world, settings):
    rng = random.Random(settings.chaos_seed)
    world.supervisor.start("agent_b2")
    targets = {"bridge": world.bridge, "actions": world.actions}
    faults_set: Counter = Counter()
    crashes_armed: Counter = Counter()

    def send(msg_id: str, text: str) -> bool:
        try:
            world.agent_a.message(text, msg_id=msg_id)
            return True
        except httpx.HTTPError:  # Agent A is down (crash point): retry with the same msg_id
            return False

    for i in range(N_MESSAGES):
        if rng.random() < 0.35:
            where, rule = random_fault(rng)
            rule["note"] = f"C1 #{i}"
            targets[where].http.post("/faults", json=rule).raise_for_status()
            faults_set[f"{where}:{rule['action']}"] += 1
        if rng.random() < 0.12:
            worker = rng.choice(["agent_b", "agent_b2"])
            world.supervisor.restart(worker, env={"CRASH_AT": rng.choice(WORKER_CRASHES)})
            crashes_armed[worker] += 1
        if rng.random() < 0.05:
            world.supervisor.restart("agent_a", env={"CRASH_AT": rng.choice(ORCHESTRATOR_CRASHES)})
            crashes_armed["agent_a"] += 1
        text = rng.choice(MESSAGES)
        wait_until(lambda i=i, text=text: send(f"c{i}", text), timeout=30, every=0.2)

    world.caption("Waiting until every task is terminal")

    def all_terminal():
        dialogs = [d for d in world.bridge.get("/dialogs") if d["msg_id"].startswith("c")]
        tasks = world.bridge.get("/tasks")
        done = len({d["msg_id"] for d in dialogs}) == N_MESSAGES and all(
            t["state"] in TERMINAL for t in tasks
        )
        return done and (dialogs, tasks)

    dialogs, tasks = wait_until(all_terminal, timeout=300, every=1)

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
