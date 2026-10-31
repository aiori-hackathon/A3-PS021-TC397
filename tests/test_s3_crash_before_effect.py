"""S3: worker crashes before the side effect (docs/TESTING.md#s3--crash-before-the-side-effect)."""

import pytest

from tests.helpers.assertions import assert_events_in_order
from tests.helpers.scenario import (
    PAY,
    checkpoint_counts,
    duplicates,
    events_of,
    process_is,
    recovery_ms,
    task_in,
)


@pytest.mark.official
def test_s3_crash_before_side_effect(world):
    world.caption("Setting trap: Agent B will crash just before paying")
    world.supervisor.restart("agent_b", env={"CRASH_AT": "before_pay"})

    plan = world.agent_a.message(PAY, msg_id="s3")
    [task_id] = plan["task_ids"]
    process_is(world, "agent_b", "crashed")
    before = world.bridge.task(task_id)
    assert before["state"] in ("IN_PROGRESS", "SUSPENDED")
    assert before["effect"]["status"] == "none"
    assert world.actions.count() == 0

    world.caption("Restarting Agent B with no crash point; it resumes from its checkpoint")
    world.supervisor.restart("agent_b")
    after = task_in(world, task_id, "COMPLETED")

    world.caption("Checking: same task, fence 2, attempt 2, one payment")
    assert after["task_id"] == before["task_id"] == task_id
    assert after["dialog_id"] == plan["dialog_id"]
    assert (after["fence"], after["attempt"]) == (2, 2)
    assert world.actions.count(task_id=task_id, action="pay") == 1
    events = events_of(world, task_id)
    assert_events_in_order(
        events,
        ["claimed", "checkpoint", "resumed", "effect_intent", "effect_committed", "completed"],
    )
    counts = checkpoint_counts(world, task_id)
    assert counts[0] == 1 and counts[1] == 1  # parse and plan were not redone
    assert counts == dict.fromkeys(range(5), 1)
    world.record(
        task_id_preserved=after["task_id"] == before["task_id"],
        side_effects=world.actions.count(task_id=task_id, action="pay"),
        duplicates_rejected=duplicates(world),
        recovery_ms=recovery_ms(world, task_id),
        fence=after["fence"],
        attempt=after["attempt"],
    )
