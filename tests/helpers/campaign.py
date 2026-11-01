"""The seeded fault campaign shared by C1 (chaos) and M2 (protection on vs off).

`run_workload` sends N messages and, before each one, draws faults and crash points
from the given random.Random. Every draw happens in a fixed order and none depends on
how the system answered, so one seed injects the same faults in the same order with
protection on or off (timing still varies between runs).

`score` judges one run per user message, so it also sees duplicates the ledger key
check cannot: with dedup off, a retried dialog.create makes a second dialog whose
tasks have new ids and therefore new effect keys.
"""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from typing import Any

import httpx

from tests.helpers.wait import wait_until

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


def run_workload(world, rng: random.Random, n_messages: int, tag: str = "C1") -> dict[str, Counter]:
    """Send n messages (msg_id c0..c{n-1}) with random faults and crash points armed."""
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

    for i in range(n_messages):
        if rng.random() < 0.35:
            where, rule = random_fault(rng)
            rule["note"] = f"{tag} #{i}"
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
    return {"faults_set": faults_set, "crashes_armed": crashes_armed}


def wait_terminal(world, n_messages: int, timeout: float) -> tuple[list, list, bool]:
    """Wait until every message has a dialog and every task is terminal.

    Returns (dialogs, tasks, settled); on timeout the last snapshot with settled=False.
    """
    snapshot: dict[str, Any] = {}

    def all_terminal():
        dialogs = [d for d in world.bridge.get("/dialogs") if d["msg_id"].startswith("c")]
        tasks = world.bridge.get("/tasks")
        snapshot.update(dialogs=dialogs, tasks=tasks)
        return len({d["msg_id"] for d in dialogs}) == n_messages and all(
            t["state"] in TERMINAL for t in tasks
        )

    try:
        wait_until(all_terminal, timeout=timeout, every=1)
        return snapshot["dialogs"], snapshot["tasks"], True
    except TimeoutError:
        return snapshot.get("dialogs", []), snapshot.get("tasks", []), False


def score(
    dialogs: list[dict], tasks: list[dict], ledger: list[dict], msg_prefix: str = "c"
) -> dict[str, Any]:
    """Outcome of one run, judged per user message.

    A message's planned side effects are the action tasks of its first dialog. Every
    ledger row beyond that plan, for the same message and action, is a duplicate side
    effect: the same key performed twice, or the work of a second dialog for one message.
    """
    dialogs = sorted(
        (d for d in dialogs if d["msg_id"].startswith(msg_prefix)),
        key=lambda d: (d["created_at"], d["dialog_id"]),
    )
    by_id = {t["task_id"]: t for t in tasks}
    msg_of: dict[str, str] = {}
    first: dict[str, dict] = {}
    for d in dialogs:
        first.setdefault(d["msg_id"], d)
        for tid in d["task_ids"]:
            msg_of[tid] = d["msg_id"]
    planned: Counter = Counter()
    for d in first.values():
        for tid in d["task_ids"]:
            action = by_id.get(tid, {}).get("action")
            if action:
                planned[(d["msg_id"], action)] += 1

    rows_for: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in sorted(ledger, key=lambda r: r["id"]):
        if r["task_id"] in msg_of:
            rows_for[(msg_of[r["task_id"]], r["action"])].append(r)
    extra_rows = [r for k, rows in rows_for.items() for r in rows[planned[k] :]]
    extra_pay = [r for r in extra_rows if r["action"] == "pay"]

    run_tasks = [t for t in tasks if t["task_id"] in msg_of]
    states = Counter(t["state"] for t in run_tasks)
    per_key = Counter(r["idempotency_key"] for rows in rows_for.values() for r in rows)
    return {
        "messages": len(first),
        "dialogs": len(dialogs),
        "extra_dialogs": len(dialogs) - len(first),
        "tasks": len(run_tasks),
        "completed": states["COMPLETED"],
        "failed": states["FAILED"],
        "cancelled": states["CANCELLED"],
        "stuck_tasks": sum(n for s, n in states.items() if s not in TERMINAL),
        "ledger_rows": sum(len(rows) for rows in rows_for.values()),
        "duplicate_side_effects": len(extra_rows),
        "duplicate_payments": len(extra_pay),
        # Only some messages name an amount ("send money to Asha" does not).
        "money_paid_twice": sum((r.get("params") or {}).get("amount") or 0 for r in extra_pay),
        "payments_without_amount": sum(
            1 for r in extra_pay if not (r.get("params") or {}).get("amount")
        ),
        "keys_performed_twice": sum(1 for n in per_key.values() if n > 1),
    }
