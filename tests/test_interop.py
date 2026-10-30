"""I3: cross-adapter correlation (docs/TESTING.md#i3--cross-adapter-correlation).

Agent A creates the work through the MCP-style entrance and Agent B works it
through the A2A-style entrance. Both views must carry the same dialog and
task IDs, and their states must correspond at every point we observe.
"""

import pytest

from bridge.codecs.a2a_style import STATE_TO_A2A
from tests.helpers.scenario import PAY, task_in
from tests.helpers.wait import wait_until


@pytest.mark.world_env(STEP_DELAY="0.2")  # so the run can be sampled step by step
def test_i3_cross_adapter_correlation(world):
    plan = world.agent_a.message(PAY, msg_id="i3")
    [task_id] = plan["task_ids"]
    dialog_id = plan["dialog_id"]

    samples = []

    def sample():
        views = world.bridge.get(f"/tasks/{task_id}/views")
        samples.append(views)
        return views["shared"]["state"] == "COMPLETED"

    wait_until(sample, timeout=15, every=0.05)
    seen = set()
    for views in samples:
        mcp, a2a, shared = (
            views["mcp_style"]["structuredContent"],
            views["a2a_style"],
            views["shared"],
        )
        assert (mcp["task_id"], mcp["dialog_id"]) == (task_id, dialog_id)
        assert (a2a["id"], a2a["contextId"]) == (task_id, dialog_id)
        assert mcp["state"] == a2a["metadata"]["bridge_state"] == shared["state"]
        assert a2a["status"]["state"] == STATE_TO_A2A[shared["state"]]
        seen.add(shared["state"])
    assert {"IN_PROGRESS", "COMPLETED"} <= seen

    # Every A2A-style reply Agent B received for this task, checkpoint by checkpoint,
    # matches the bridge's own record of the state at that event.
    replies = [
        m
        for m in world.agent_b.state()["messages"]
        if m["request"]["message"].get("taskId") == task_id
    ]
    checkpoints = [m for m in replies if m["op"] == "task.checkpoint"]
    assert len(checkpoints) == 5
    events = world.bridge.events(task_id=task_id)
    for m in checkpoints:
        view = m["reply"]["task"]
        assert (view["id"], view["contextId"]) == (task_id, dialog_id)
        seq = view["metadata"]["event_seq"]
        moves = [e for e in events if e["type"] == "state_changed" and e["seq"] <= seq]
        assert view["metadata"]["bridge_state"] == moves[-1]["data"]["to"]
        assert view["status"]["state"] == STATE_TO_A2A[view["metadata"]["bridge_state"]]

    # And Agent A's side: an MCP-style dialog.get returns the exact final state.
    status, reply = world.bridge.mcp("dialog.get", dialog_id=dialog_id)
    assert status == 200
    [mcp_task] = reply.result["tasks"]
    assert (mcp_task["task_id"], mcp_task["state"]) == (task_id, "COMPLETED")
    task_in(world, task_id, "COMPLETED")
