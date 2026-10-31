"""S0: happy path, everything works with no faults (docs/TESTING.md#s0--happy-path)."""

from tests.helpers.assertions import assert_events_in_order
from tests.helpers.wait import wait_until

TEXT = "Pay ₹40 to the bakery and email the receipt to Rahul"


def test_s0_happy_path(world):
    world.caption("S0: normal path, no faults")
    plan = world.agent_a.message(TEXT, msg_id="s0")
    dialog_id = plan["dialog_id"]
    t1, t2 = plan["task_ids"]
    assert [t["intent"] for t in plan["tasks"]] == ["payment", "message"]

    world.caption("Waiting for both tasks to complete")
    wait_until(
        lambda: (
            [t["state"] for t in world.bridge.dialog(dialog_id)["tasks"]]
            == ["COMPLETED", "COMPLETED"]
        ),
        timeout=15,
    )

    world.caption("Checking the ledger: 1 payment, 1 message")
    assert world.actions.count(action="pay") == 1
    assert world.actions.count(action="send") == 1
    assert world.actions.count(task_id=t1, action="pay") == 1
    assert world.actions.count(task_id=t2, action="send") == 1

    tasks = {t["task_id"]: t for t in world.bridge.dialog(dialog_id)["tasks"]}
    assert set(tasks) == {t1, t2}  # identity: the same IDs the plan announced
    assert all(t["dialog_id"] == dialog_id for t in tasks.values())
    assert all((t["fence"], t["attempt"]) == (1, 1) for t in tasks.values())
    assert tasks[t1]["effect"]["status"] == "committed"
    assert tasks[t1]["effect"]["key"] == f"{t1}:pay"

    e1 = world.bridge.events(task_id=t1)
    e2 = world.bridge.events(task_id=t2)
    for events, task_id in ((e1, t1), (e2, t2)):
        assert_events_in_order(
            events, ["claimed", "checkpoint", "effect_intent", "effect_committed", "completed"]
        )
        checkpoints = [e["data"]["step_index"] for e in events if e["type"] == "checkpoint"]
        assert checkpoints == list(range(len(tasks[task_id]["steps"])))
    completed_t1 = next(e["seq"] for e in e1 if e["type"] == "completed")
    claimed_t2 = next(e["seq"] for e in e2 if e["type"] == "claimed")
    assert claimed_t2 > completed_t1  # the email waited for the payment

    wait_until(
        lambda: any(
            m["kind"] == "summary" and m["dialog_id"] == dialog_id for m in world.agent_a.chat()
        ),
        timeout=5,
    )
    metrics = world.bridge.get("/metrics")
    assert metrics["tasks_completed"] == 2
    assert metrics["avg_task_ms"] is not None  # baseline for RESULTS.md
    assert metrics["duplicate_effects_rejected"] == 0
    world.record(
        avg_task_ms=metrics["avg_task_ms"],
        events_per_task=round((len(e1) + len(e2)) / 2, 1),
        side_effects=world.actions.count(),
    )
