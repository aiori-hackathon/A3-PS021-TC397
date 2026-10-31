"""S1: lost reply, sender retries (docs/TESTING.md#s1--lost-reply-sender-retries)."""

import pytest

from tests.helpers.scenario import PAY, add_fault, duplicates, task_in


@pytest.mark.official
def test_s1_lost_reply_sender_retries(world):
    world.caption("Setting trap: the reply to Agent A's dialog.create will be lost once")
    rule = add_fault(
        world.bridge,
        link="agent_a",
        action="drop_reply",
        match={"op": "dialog.create"},
        note="S1: lose the reply to dialog.create once",
    )

    world.caption("Agent A sends the request; the bridge commits it but the reply is lost")
    plan = world.agent_a.message(PAY, msg_id="s1")
    [task_id] = plan["task_ids"]
    task = task_in(world, task_id, "COMPLETED", timeout=10)

    world.caption("Checking: one dialog, one task, one payment")
    dialogs = [d for d in world.bridge.get("/dialogs") if d["msg_id"] == "s1"]
    assert len(dialogs) == 1
    assert dialogs[0]["task_ids"] == [task_id] == [task["task_id"]]
    assert dialogs[0]["dialog_id"] == plan["dialog_id"]
    applied = [
        e for e in world.bridge.events(type="fault_applied") if e["data"]["rule_id"] == rule["id"]
    ]
    assert len(applied) == 1
    create_repeats = [
        e
        for e in world.bridge.events(type="duplicate_request")
        if e["data"]["request_id"] == "s1:create"
    ]
    assert len(create_repeats) >= 1  # the retry got the stored reply
    assert world.actions.count(task_id=task_id, action="pay") == 1
    assert world.actions.count() == 1
    world.record(
        task_id_preserved=True,
        dialogs_for_msg=len(dialogs),
        side_effects=world.actions.count(task_id=task_id, action="pay"),
        duplicates_rejected=duplicates(world),
    )
