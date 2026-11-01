"""M1: protection off (docs/TESTING.md#m1--protection-off-mutation-check).

With DISABLE_DEDUP=1 the bridge inbox and the ledger key check are off. S1 and
S4 must then show duplicates. These tests PASS when the duplicates appear,
which proves S1 and S4 would catch broken dedup.

    DISABLE_DEDUP=1 pytest -m mutation
"""

import pytest

from tests.helpers.scenario import PAY, add_fault, process_is, task_in
from tests.helpers.wait import wait_until

pytestmark = [pytest.mark.mutation, pytest.mark.world_env(DISABLE_DEDUP="1")]


def test_m1_s1_protection_off(world):
    world.caption("Protection OFF: S1 again, the lost reply is retried")
    add_fault(
        world.bridge,
        link="agent_a",
        action="drop_reply",
        match={"op": "dialog.create"},
        note="M1/S1: lose the reply to dialog.create once",
    )
    plan = world.agent_a.message(PAY, msg_id="s1")
    task_in(world, plan["task_ids"][0], "COMPLETED")
    dialogs = wait_until(
        lambda: (
            (d := [x for x in world.bridge.get("/dialogs") if x["msg_id"] == "s1"])
            and len(d) >= 2
            and d
        ),
        timeout=5,
    )
    world.record(
        dialogs_for_msg=len(dialogs),
        side_effects=world.actions.count(action="pay"),
        key_number=f"protection off: {len(dialogs)} dialogs for one message",
    )
    assert len(dialogs) >= 2  # the retry created a second dialog: dedup really was off


def test_m1_s4_protection_off(world):
    world.caption("Protection OFF: S4 again, Agent B crashes right after paying")
    world.supervisor.restart("agent_b", env={"CRASH_AT": "after_pay_call"})
    plan = world.agent_a.message(PAY, msg_id="s4")
    [task_id] = plan["task_ids"]
    process_is(world, "agent_b", "crashed")
    world.supervisor.restart("agent_b")
    task_in(world, task_id, "COMPLETED")
    payments = world.actions.count(task_id=task_id, action="pay")
    world.record(side_effects=payments, key_number=f"protection off: {payments} payments")
    assert payments == 2  # the retry with the same key paid again
