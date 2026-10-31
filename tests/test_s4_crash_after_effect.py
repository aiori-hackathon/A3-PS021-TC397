"""S4: worker crashes after the side effect, before reporting it
(docs/TESTING.md#s4--crash-after-the-side-effect-before-reporting)."""

import pytest

from tests.helpers.scenario import PAY, duplicates, events_of, process_is, recovery_ms, task_in


@pytest.mark.official
def test_s4_crash_after_side_effect(world):
    world.caption("Setting trap: Agent B crashes right after paying, before telling the bridge")
    world.supervisor.restart("agent_b", env={"CRASH_AT": "after_pay_call"})

    plan = world.agent_a.message(PAY, msg_id="s4")
    [task_id] = plan["task_ids"]
    process_is(world, "agent_b", "crashed")

    [paid] = world.actions.ledger(task_id=task_id, action="pay")
    assert paid["idempotency_key"] == f"{task_id}:pay"
    stuck = world.bridge.task(task_id)
    assert stuck["effect"]["status"] == "intent"  # the bridge does not know it happened
    assert stuck["state"] in ("EFFECT_PENDING", "SUSPENDED")

    world.caption("Restarting Agent B: it retries the payment with the same key")
    world.supervisor.restart("agent_b")
    done = task_in(world, task_id, "COMPLETED")

    world.caption("Checking the ledger: 1 payment")
    assert world.actions.count(task_id=task_id, action="pay") == 1
    assert world.actions.count() == 1
    assert done["task_id"] == task_id
    assert done["effect"]["receipt_id"] == paid["receipt_id"]  # the original receipt
    assert done["effect"]["replayed"] is True
    assert (done["fence"], done["attempt"]) == (2, 2)
    assert "duplicate_rejected" in [e["type"] for e in events_of(world, task_id)]
    metrics = world.bridge.get("/metrics")
    assert metrics["duplicate_effects_rejected"] == 1
    world.record(
        task_id_preserved=done["task_id"] == task_id,
        side_effects=world.actions.count(task_id=task_id, action="pay"),
        duplicates_rejected=duplicates(world),
        recovery_ms=recovery_ms(world, task_id),
        same_receipt=done["effect"]["receipt_id"] == paid["receipt_id"],
    )
