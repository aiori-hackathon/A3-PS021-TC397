"""S5: bridge hard-killed during a network cut (docs/TESTING.md#s5--bridge-hard-killed-during-a-network-cut)."""

import pytest

from tests.helpers.scenario import (
    PAY,
    add_fault,
    chat_has,
    duplicates,
    ms_between,
    process_is,
    task_in,
)
from tests.helpers.wait import wait_until


def cut_applied(world, rule_id):
    return any(e["data"]["rule_id"] == rule_id for e in world.bridge.events(type="fault_applied"))


@pytest.mark.official
@pytest.mark.world_env(STEP_DELAY="0.5")  # slow steps so the kill lands mid-task
def test_s5_bridge_hard_kill_during_network_cut(world):
    plan = world.agent_a.message(PAY, msg_id="s5")
    [task_id] = plan["task_ids"]
    wait_until(lambda: world.bridge.task(task_id)["last_checkpoint"] is not None, timeout=10)

    world.caption("Cutting the link between Agent A and the bridge")
    cut = add_fault(world.bridge, link="agent_a", action="cut", count=-1, note="S5: network cut")
    wait_until(lambda: cut_applied(world, cut["id"]), timeout=5)
    last_seen = world.bridge.health()["last_seq"]
    assert world.bridge.task(task_id)["state"] in ("IN_PROGRESS", "EFFECT_PENDING")

    world.caption("Hard-killing the bridge mid-task")
    world.supervisor.kill("bridge")
    process_is(world, "bridge", "crashed")
    world.supervisor.start("bridge")

    world.caption("Bridge restarted; fault rules were in memory, so re-apply the cut")
    cut2 = add_fault(world.bridge, link="agent_a", action="cut", count=-1, note="S5: cut again")
    wait_until(lambda: cut_applied(world, cut2["id"]), timeout=5)  # A really hit it
    world.bridge.http.delete(f"/faults/{cut2['id']}").raise_for_status()

    world.caption("Checking: same task, no gap in the event sequence, one payment")
    started = world.bridge.events(type="bridge_started")
    assert len(started) == 2
    restart = started[-1]
    assert restart["data"]["previous_last_seq"] >= last_seen
    assert restart["seq"] == restart["data"]["previous_last_seq"] + 1
    assert restart["data"]["recovered_tasks"] >= 1

    done = task_in(world, task_id, "COMPLETED", timeout=15)
    assert done["task_id"] == task_id and done["dialog_id"] == plan["dialog_id"]
    assert world.actions.count(task_id=task_id, action="pay") == 1
    seqs = [e["seq"] for e in world.bridge.events()]
    assert seqs == list(range(1, len(seqs) + 1))

    world.caption("Agent A catches up with events.since and reports completion")
    chat_has(world, "summary", plan["dialog_id"], timeout=10)
    notices = [m for m in world.agent_a.chat() if m["kind"] == "notice"]
    assert any("Bridge restarted" in m["text"] for m in notices)
    all_events = world.bridge.events()
    before_restart = [e for e in all_events if e["seq"] < restart["seq"]]
    world.record(
        task_id_preserved=done["task_id"] == task_id,
        side_effects=world.actions.count(task_id=task_id, action="pay"),
        duplicates_rejected=duplicates(world),
        recovery_ms=ms_between(before_restart[-1]["ts"], restart["ts"]),
        seq_continued=restart["seq"] == restart["data"]["previous_last_seq"] + 1,
        recovered_tasks=restart["data"]["recovered_tasks"],
    )
