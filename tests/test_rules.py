"""R1-R4: the bridge enforces its rules (docs/TESTING.md#r1r4--rules).

Phase 2: run against a bare bridge process, calling /a2a and /mcp directly.
"""

from tests.helpers import flows
from tests.helpers.flows import GENERAL


def test_r1_invalid_transition(bridge, uid):
    tid = flows.dispatched_task(bridge, uid)
    status, reply = flows.claim(bridge, tid, "agent_b")
    assert status == 200, reply
    task = reply.result
    flows.run_steps(bridge, task, fence=1, start=0, stop=len(task["steps"]))
    status, reply = flows.worker_write(bridge, "task.complete", task, 1, {"result": {"ok": 1}})
    assert status == 200 and reply.result["state"] == "COMPLETED"

    # Try to move the COMPLETED task back to IN_PROGRESS by claiming it.
    status, reply = flows.claim(bridge, tid, "agent_b2")
    assert status == 409
    assert reply.error.code == "invalid_transition"
    assert reply.error.details["from"] == "COMPLETED"
    assert reply.error.details["to"] == "IN_PROGRESS"
    after = bridge.task(tid)
    assert after["state"] == "COMPLETED" and after["owner"] == "agent_b" and after["fence"] == 1
    assert "invalid_transition_rejected" in bridge.event_types(task_id=tid)


def test_r2_unknown_schema_version(bridge, uid):
    payload = {"msg_id": uid, "text": "x", "tasks": [GENERAL]}
    for send in (bridge.mcp, bridge.a2a):
        status, reply = send(
            "dialog.create",
            request_id=f"{uid}:create",
            payload=payload,
            schema="dialog-state/9.9",
        )
        assert status == 400
        assert reply.error.code == "schema_unsupported"
    assert not [d for d in bridge.get("/dialogs") if d["msg_id"] == uid]
    rejected = [
        e
        for e in bridge.events(type="schema_rejected")
        if e["data"]["request_id"] == f"{uid}:create"
    ]
    assert len(rejected) == 2


def test_r2_unknown_schema_with_unknown_fields_is_still_schema_unsupported(bridge, uid):
    # A future version may change the payload; the version decides, not the fields.
    native = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "dialog.create",
            "arguments": {"msg_id": uid, "future_field": True},
            "_meta": {
                "schema": "dialog-state/9.9",
                "request_id": f"{uid}:create",
                "sender": "agent_a",
            },
        },
    }
    status, reply = bridge.mcp_native(native)
    assert status == 400 and reply.error.code == "schema_unsupported"


def test_r3_stale_fence(bridge, uid):
    tid = flows.dispatched_task(bridge, uid)
    status, reply = flows.claim(bridge, tid, "agent_b")
    assert status == 200 and reply.result["fence"] == 1
    task = reply.result
    # The same agent restarts and resumes: the fence moves to 2.
    status, reply = flows.resume(bridge, "agent_b", boot=f"{uid}-boot2")
    assert status == 200
    assert [t["fence"] for t in reply.result["tasks"] if t["task_id"] == tid] == [2]

    status, reply = flows.checkpoint(bridge, task, fence=1, step_index=0)
    assert status == 409
    assert reply.error.code == "stale_fence"
    assert reply.error.details["current_fence"] == 2
    assert bridge.task(tid)["last_checkpoint"] is None
    types = bridge.event_types(task_id=tid)
    assert "checkpoint" not in types
    assert "stale_fence_blocked" in types


def test_r4_reused_request_id_with_different_body(bridge, uid):
    rid = f"{uid}:create"
    payload = {"msg_id": uid, "text": "first", "tasks": [GENERAL]}
    status, first = bridge.mcp("dialog.create", request_id=rid, payload=payload)
    assert status == 200

    status, reply = bridge.mcp(
        "dialog.create", request_id=rid, payload={**payload, "text": "different"}
    )
    assert status == 422
    assert reply.error.code == "request_id_conflict"

    # The original stored reply is unchanged.
    status, again = bridge.mcp("dialog.create", request_id=rid, payload=payload)
    assert status == 200
    assert again == first
    assert [d["msg_id"] for d in bridge.get("/dialogs")].count(uid) == 1
    conflicts = [
        e for e in bridge.events(type="request_id_conflict") if e["data"]["request_id"] == rid
    ]
    assert len(conflicts) == 1
