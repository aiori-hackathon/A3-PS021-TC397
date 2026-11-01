"""Bridge API behaviour through the public entrances (/mcp, /a2a, read-only routes, /ws).

Phase 2 "done when": a dialog can be created and a task walked to COMPLETED by
hand. test_walk_payment_then_message_to_completed is that walk, automated.
"""

import json
from concurrent.futures import ThreadPoolExecutor

import httpx
from websockets.sync.client import connect

from tests.helpers import flows
from tests.helpers.assertions import assert_events_in_order
from tests.helpers.clients import now_iso
from tests.helpers.flows import GENERAL, MESSAGE, PAYMENT


def test_walk_payment_then_message_to_completed(bridge, uid):
    created = flows.create_dialog(bridge, uid, [PAYMENT, MESSAGE], text="pay and email")
    t1, t2 = created["task_ids"]
    assert created["dialog_id"].startswith("dlg_") and len(created["dialog_id"]) == 16
    assert t1.startswith("tsk_") and len(t1) == 16

    flows.wait_state(bridge, t1, "AWAITING_CONFIRMATION")
    assert bridge.task(t2)["state"] == "CREATED"  # waits for task 1
    flows.confirm(bridge, t1)
    flows.confirm(bridge, t2)
    flows.wait_state(bridge, t1, "DISPATCHED")
    assert bridge.task(t2)["state"] == "CREATED"

    # Worker walks the payment task.
    status, reply = flows.claim(bridge, t1)
    assert status == 200
    task = reply.result
    assert (task["state"], task["owner"], task["fence"], task["attempt"]) == (
        "IN_PROGRESS",
        "agent_b",
        1,
        1,
    )
    assert task["lease_until"] is not None
    flows.run_steps(bridge, task, 1, 0, 2)
    key = f"{t1}:pay"
    status, reply = flows.worker_write(
        bridge,
        "task.effect_intent",
        task,
        1,
        {"effect_key": key, "action": "pay", "params": task["params"]},
    )
    assert status == 200
    assert reply.result["state"] == "EFFECT_PENDING"
    assert reply.result["effect"] == {
        "key": key,
        "status": "intent",
        "receipt_id": None,
        "replayed": False,
    }
    status, reply = flows.worker_write(
        bridge, "task.effect_result", task, 1, {"receipt_id": "rcp_000000000001", "replayed": False}
    )
    assert status == 200
    assert reply.result["state"] == "EFFECT_PENDING"  # effect_result never changes state
    assert reply.result["effect"]["status"] == "committed"
    flows.run_steps(bridge, task, 1, 2, 5)
    status, reply = flows.worker_write(bridge, "task.complete", task, 1, {"result": {"paid": 40}})
    assert status == 200 and reply.result["state"] == "COMPLETED"
    assert reply.result["result"] == {"paid": 40}
    assert reply.result["last_checkpoint"]["step_index"] == 4

    # The dependent task now becomes ready (it was confirmed earlier).
    flows.wait_state(bridge, t2, "DISPATCHED")
    status, reply = flows.claim(bridge, t2)
    assert status == 200
    task2 = reply.result
    flows.run_steps(bridge, task2, 1, 0, 2)
    flows.worker_write(
        bridge,
        "task.effect_intent",
        task2,
        1,
        {"effect_key": f"{t2}:send", "action": "send", "params": {}},
    )
    flows.worker_write(bridge, "task.effect_result", task2, 1, {"receipt_id": "rcp_000000000002"})
    flows.run_steps(bridge, task2, 1, 2, 4)
    status, reply = flows.worker_write(bridge, "task.complete", task2, 1)
    assert status == 200 and reply.result["state"] == "COMPLETED"

    dialog = bridge.dialog(created["dialog_id"])
    assert [t["state"] for t in dialog["tasks"]] == ["COMPLETED", "COMPLETED"]
    assert dialog["dialog"]["msg_id"] == uid

    e1 = bridge.events(task_id=t1)
    assert_events_in_order(
        e1,
        [
            "task_created",
            "confirmed",
            "claimed",
            "checkpoint",
            "effect_intent",
            "effect_committed",
            "completed",
        ],
    )
    completed_t1 = next(e["seq"] for e in e1 if e["type"] == "completed")
    claimed_t2 = next(e["seq"] for e in bridge.events(task_id=t2) if e["type"] == "claimed")
    assert claimed_t2 > completed_t1


def test_a2a_reply_carries_exact_bridge_state(bridge, uid):
    tid = flows.dispatched_task(bridge, uid)
    flows.claim(bridge, tid)
    native = bridge.http.post(
        "/a2a",
        json={
            "message": {
                "messageId": f"{tid}:checkpoint:0:1",
                "role": "agent",
                "kind": "task.checkpoint",
                "taskId": tid,
                "parts": [
                    {"kind": "data", "data": {"step_index": 0, "step": "parse", "output": {}}}
                ],
                "metadata": {
                    "schema": "dialog-state/0.1-experimental",
                    "sender": "agent_b",
                    "fence": 1,
                    "sent_at": now_iso(),
                },
            }
        },
    ).json()
    assert native["task"]["id"] == tid
    assert native["task"]["status"]["state"] == "working"
    assert native["task"]["metadata"]["bridge_state"] == "IN_PROGRESS"
    assert native["task"]["metadata"]["event_seq"] > 0


def test_mcp_reply_shape(bridge, uid):
    native = {
        "jsonrpc": "2.0",
        "id": 77,
        "method": "tools/call",
        "params": {
            "name": "dialog.create",
            "arguments": {"msg_id": uid, "text": "x", "tasks": [GENERAL]},
            "_meta": {
                "schema": "dialog-state/0.1-experimental",
                "request_id": f"{uid}:create",
                "sender": "agent_a",
                "sent_at": now_iso(),
            },
        },
    }
    out = bridge.http.post("/mcp", json=native).json()
    assert out["jsonrpc"] == "2.0" and out["id"] == 77
    assert out["result"]["isError"] is False
    assert out["result"]["structuredContent"]["dialog_id"].startswith("dlg_")
    assert out["result"]["_meta"]["event_seq"] > 0


def test_duplicate_request_returns_the_stored_reply(bridge, uid):
    tid = flows.dispatched_task(bridge, uid)
    task = flows.claim(bridge, tid)[1].result
    first = flows.checkpoint(bridge, task, 1, 0)
    # A retry: same request_id, same body, new sent_at.
    second = flows.checkpoint(bridge, task, 1, 0)
    assert first[0] == second[0] == 200
    assert first[1] == second[1]  # byte-for-byte the same reply, including event_seq
    types = bridge.event_types(task_id=tid)
    assert types.count("checkpoint") == 1
    assert types.count("duplicate_request") == 1


def test_stored_error_is_returned_again(bridge, uid):
    tid = flows.dispatched_task(bridge, uid)
    flows.claim(bridge, tid, "agent_b")
    a = flows.claim(bridge, tid, "agent_b2")
    b = flows.claim(bridge, tid, "agent_b2")
    assert a[0] == b[0] == 409
    assert a[1] == b[1]
    assert a[1].error.code == "already_claimed"


def test_two_simultaneous_claims_one_winner(bridge, uid):
    tid = flows.dispatched_task(bridge, uid)

    def go(agent):
        from tests.helpers.clients import BridgeClient

        client = BridgeClient(bridge.base_url)
        try:
            return flows.claim(client, tid, agent)
        finally:
            client.close()

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(go, ["agent_b", "agent_b2"]))
    codes = sorted(status for status, _ in results)
    assert codes == [200, 409]
    loser = next(reply for status, reply in results if status == 409)
    assert loser.error.code == "already_claimed"
    task = bridge.task(tid)
    assert task["fence"] == 1 and task["attempt"] == 1
    assert bridge.event_types(task_id=tid).count("claimed") == 1


def test_claim_next_and_resume(fresh_bridge):
    bridge = fresh_bridge
    status, reply = bridge.a2a("task.claim_next", request_id="agent_b:claim_next:boot1:1")
    assert status == 200 and reply.result is None

    tid = flows.dispatched_task(bridge, "cn")
    status, reply = bridge.a2a("task.claim_next", request_id="agent_b:claim_next:boot1:2")
    assert status == 200
    assert reply.result["task_id"] == tid
    assert (reply.result["fence"], reply.result["attempt"]) == (1, 1)
    status, reply = bridge.a2a(
        "task.claim_next", request_id="agent_b2:claim_next:boot1:1", sender="agent_b2"
    )
    assert reply.result is None  # nothing else is free

    # The owner restarts quickly and resumes before its lease expired.
    status, reply = flows.resume(bridge, "agent_b", boot="boot2")
    assert status == 200
    [resumed] = reply.result["tasks"]
    assert (resumed["task_id"], resumed["fence"], resumed["attempt"]) == (tid, 2, 2)
    assert resumed["state"] == "IN_PROGRESS"
    # Another agent owns nothing, so it gets nothing back.
    status, reply = flows.resume(bridge, "agent_b2", boot="boot1")
    assert reply.result == {"tasks": []}
    assert_events_in_order(bridge.events(task_id=tid), ["claimed", "resumed"])


def test_effect_protocol_rules(bridge, uid):
    tid = flows.dispatched_task(bridge, uid, PAYMENT)
    task = flows.claim(bridge, tid)[1].result
    flows.run_steps(bridge, task, 1, 0, 2)
    key = f"{tid}:pay"

    status, reply = flows.worker_write(
        bridge, "task.complete", task, 1, key=f"{tid}:complete:1:early"
    )
    assert status == 409 and reply.error.code == "invalid_transition"

    status, reply = flows.worker_write(
        bridge, "task.effect_result", task, 1, {"receipt_id": "rcp_x"}
    )
    assert status == 409 and reply.error.code == "invalid_transition"

    status, reply = flows.worker_write(
        bridge,
        "task.effect_intent",
        task,
        1,
        {"effect_key": f"{tid}:pay:1", "action": "pay"},
        key=f"{tid}:effect_intent:1:badkey",
    )
    assert status == 400 and reply.error.code == "validation_error"

    status, reply = flows.worker_write(
        bridge, "task.effect_intent", task, 1, {"effect_key": key, "action": "pay"}
    )
    assert status == 200 and reply.result["state"] == "EFFECT_PENDING"

    # The side effect may have happened: cancelling is refused from now on.
    status, reply = bridge.mcp("task.cancel", request_id=f"{tid}:cancel", task_id=tid)
    assert status == 409 and reply.error.code == "invalid_transition"

    # The worker restarts; the task comes back IN_PROGRESS with effect.status = intent.
    status, reply = flows.resume(bridge, "agent_b", boot=f"{uid}-b2")
    [task2] = [t for t in reply.result["tasks"] if t["task_id"] == tid]
    assert task2["state"] == "IN_PROGRESS"
    assert task2["effect"]["status"] == "intent" and task2["fence"] == 2
    # Repeating the intent with the same key is allowed and goes back to EFFECT_PENDING.
    status, reply = flows.worker_write(
        bridge, "task.effect_intent", task2, 2, {"effect_key": key, "action": "pay"}
    )
    assert status == 200 and reply.result["state"] == "EFFECT_PENDING"
    status, reply = flows.worker_write(
        bridge, "task.effect_result", task2, 2, {"receipt_id": "rcp_abc", "replayed": True}
    )
    assert reply.result["effect"] == {
        "key": key,
        "status": "committed",
        "receipt_id": "rcp_abc",
        "replayed": True,
    }
    assert "duplicate_rejected" in bridge.event_types(task_id=tid)
    flows.run_steps(bridge, task2, 2, 2, 5)
    status, reply = flows.worker_write(bridge, "task.complete", task2, 2)
    assert reply.result["state"] == "COMPLETED"


def test_cancel_before_side_effect_cancels_dependents(bridge, uid):
    t1, t2 = flows.create_dialog(bridge, uid, [PAYMENT, MESSAGE])["task_ids"]
    flows.wait_state(bridge, t1, "AWAITING_CONFIRMATION")
    status, reply = bridge.mcp(
        "task.cancel", request_id=f"{t1}:cancel", task_id=t1, payload={"reason": "user"}
    )
    assert status == 200 and reply.result["state"] == "CANCELLED"
    t2_after = flows.wait_state(bridge, t2, "CANCELLED")
    assert t2_after["reason"] == "dependency_failed"
    assert "claimed" not in bridge.event_types(task_id=t2)


def test_cancel_in_progress_without_effect(bridge, uid):
    tid = flows.dispatched_task(bridge, uid, PAYMENT)
    flows.claim(bridge, tid)
    status, reply = bridge.mcp("task.cancel", request_id=f"{tid}:cancel", task_id=tid)
    assert status == 200
    assert reply.result["state"] == "CANCELLED" and reply.result["lease_until"] is None


def test_non_retryable_failure_cancels_dependents(bridge, uid):
    t1, t2 = flows.create_dialog(bridge, uid, [GENERAL, {**GENERAL, "depends_on": [1]}])["task_ids"]
    flows.wait_state(bridge, t1, "DISPATCHED")
    task = flows.claim(bridge, t1)[1].result
    status, reply = flows.worker_write(
        bridge, "task.fail", task, 1, {"reason": "bad input", "retryable": False}
    )
    assert status == 200 and reply.result["state"] == "FAILED"
    assert reply.result["reason"] == "bad input"
    assert flows.wait_state(bridge, t2, "CANCELLED")["reason"] == "dependency_failed"


def test_retryable_failure_suspends_and_can_be_resumed(bridge, uid):
    tid = flows.dispatched_task(bridge, uid)
    task = flows.claim(bridge, tid)[1].result
    status, reply = flows.worker_write(
        bridge, "task.fail", task, 1, {"reason": "upstream 503", "retryable": True}
    )
    assert status == 200
    assert reply.result["state"] == "SUSPENDED" and reply.result["lease_until"] is None
    status, reply = flows.claim(bridge, tid, "agent_b2")
    assert status == 200
    assert (reply.result["owner"], reply.result["fence"], reply.result["attempt"]) == (
        "agent_b2",
        2,
        2,
    )
    assert "reclaimed" in bridge.event_types(task_id=tid)


def test_checkpoint_rules(bridge, uid):
    tid = flows.dispatched_task(bridge, uid)
    task = flows.claim(bridge, tid, "agent_b")[1].result
    step0 = {"step_index": 0, "step": "parse", "output": {}}
    # Right fence, wrong agent (its own request_id, so it cannot occupy the owner's key).
    status, reply = flows.worker_write(
        bridge, "task.checkpoint", task, 1, step0, agent="agent_b2", key=f"{tid}:impostor"
    )
    assert status == 409 and reply.error.code == "not_owner"
    # Fence from the future.
    status, reply = flows.checkpoint(bridge, task, 5, 0)
    assert status == 409 and reply.error.code == "not_owner"
    assert flows.checkpoint(bridge, task, 1, 0)[0] == 200
    flows.resume(bridge, "agent_b", boot=f"{uid}-b2")
    # A new owner incarnation may overwrite a step with its higher fence.
    status, reply = flows.checkpoint(bridge, task, 2, 0, output={"again": True})
    assert status == 200
    cps = [e for e in bridge.events(task_id=tid) if e["type"] == "checkpoint"]
    assert [(e["data"]["fence"], e["data"]["replaced"]) for e in cps] == [(1, False), (2, True)]


def test_heartbeat_extends_only_owned_leases(bridge, uid):
    tid = flows.dispatched_task(bridge, uid)
    before = flows.claim(bridge, tid, "agent_b")[1].result["lease_until"]
    status, reply = bridge.a2a(
        "agent.heartbeat", request_id=f"agent_b:hb:{uid}:1", payload={"task_ids": [tid]}
    )
    assert status == 200
    assert reply.result["leases"][tid] >= before
    status, reply = bridge.a2a(
        "agent.heartbeat",
        request_id=f"agent_b2:hb:{uid}:1",
        sender="agent_b2",
        payload={"task_ids": [tid]},
    )
    assert reply.result["leases"] == {}


def test_register_and_registry(bridge, uid):
    status, _ = bridge.a2a(
        "agent.register",
        request_id=f"agent_{uid}:register:boot1",
        sender=f"agent_{uid}",
        payload={"protocol": "a2a-style", "base_url": "http://127.0.0.1:9999"},
    )
    assert status == 200
    agents = {a["agent_id"]: a for a in bridge.get("/agents")}
    assert agents[f"agent_{uid}"]["protocol"] == "a2a-style"
    assert agents[f"agent_{uid}"]["status"] == "online"


def test_views_share_ids(bridge, uid):
    tid = flows.dispatched_task(bridge, uid)
    views = bridge.get(f"/tasks/{tid}/views")
    shared, mcp, a2a = views["shared"], views["mcp_style"], views["a2a_style"]
    assert mcp["structuredContent"]["task_id"] == a2a["id"] == shared["task_id"] == tid
    assert mcp["structuredContent"]["dialog_id"] == a2a["contextId"] == shared["dialog_id"]
    assert mcp["structuredContent"]["state"] == a2a["metadata"]["bridge_state"] == "DISPATCHED"
    assert a2a["status"]["state"] == "submitted"


def test_events_since_and_filters(bridge, uid):
    start = bridge.health()["last_seq"]
    tid = flows.dispatched_task(bridge, uid)
    status, reply = bridge.mcp("events.since", payload={"seq": start})
    assert status == 200
    events = reply.result["events"]
    assert events and all(e["seq"] > start for e in events)
    assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)
    assert reply.result["last_seq"] >= events[-1]["seq"]
    only = bridge.events(since=start, task_id=tid, type="task_created")
    assert [e["type"] for e in only] == ["task_created"]


def test_health_reports_last_seq(fresh_bridge, uid):
    # Private bridge: no background watchdog events from other tests' tasks.
    bridge = fresh_bridge
    flows.create_dialog(bridge, uid, [GENERAL])
    last = bridge.health()["last_seq"]
    assert last == max(e["seq"] for e in bridge.events(since=last - 5))


def test_websocket_hello_live_events_and_catch_up(fresh_bridge, uid):
    # Private bridge: no background watchdog events from other tests' tasks.
    bridge = fresh_bridge
    ws_url = bridge.base_url.replace("http://", "ws://") + "/ws"
    with connect(ws_url, open_timeout=5) as ws:
        hello = json.loads(ws.recv(timeout=5))
        assert hello["type"] == "hello"
        assert hello["boot_id"] == bridge.health()["boot_id"]
        start = hello["last_seq"]
        created = flows.create_dialog(bridge, uid, [GENERAL])
        live = json.loads(ws.recv(timeout=5))
        assert live["type"] == "event" and live["event"]["seq"] == start + 1
        assert live["event"]["dialog_id"] == created["dialog_id"]
        # Ask for an older window again. Live events may still be in flight, so
        # skip until the replay restarts at start + 1; from there it is in order.
        ws.send(json.dumps({"type": "since", "seq": start}))
        seq = None
        for _ in range(50):
            seq = json.loads(ws.recv(timeout=5))["event"]["seq"]
            if seq == start + 1:
                break
        assert seq == start + 1
        assert json.loads(ws.recv(timeout=5))["event"]["seq"] == start + 2


def test_validation_errors(bridge, uid):
    r = bridge.http.post("/mcp", content=b"{not json")
    assert r.status_code == 400 and r.json()["error"]["message"] == "validation_error"
    r = bridge.http.post("/a2a", content=b"[]")
    assert r.status_code == 400 and r.json()["error"]["code"] == "validation_error"
    # A state-changing op without a request_id (built by hand: the client model refuses it).
    status, reply = bridge.a2a_native(
        {
            "message": {
                "messageId": None,
                "role": "agent",
                "kind": "task.claim_next",
                "parts": [{"kind": "data", "data": {}}],
                "metadata": {
                    "schema": "dialog-state/0.1-experimental",
                    "sender": "agent_b",
                    "sent_at": now_iso(),
                },
            }
        }
    )
    assert status == 400 and reply.error.code == "validation_error"
    # depends_on must point at an earlier task (hand-built: the client model refuses it).
    status, reply = bridge.mcp_native(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "dialog.create",
                "arguments": {
                    "msg_id": uid,
                    "text": "x",
                    "tasks": [{**GENERAL, "depends_on": [2]}],
                },
                "_meta": {
                    "schema": "dialog-state/0.1-experimental",
                    "request_id": f"{uid}:create",
                    "sender": "agent_a",
                    "sent_at": now_iso(),
                },
            },
        }
    )
    assert status == 400 and reply.error.code == "validation_error"
    status, reply = flows.claim(bridge, "tsk_000000000000")
    assert status == 404 and reply.error.code == "not_found"
    assert bridge.http.get("/tasks/tsk_000000000000").status_code == 404


def test_confirm_rules(bridge, uid):
    tid = flows.dispatched_task(bridge, uid, PAYMENT)
    # Confirming again later is harmless.
    status, reply = bridge.mcp("task.confirm", request_id=f"{tid}:confirm:again", task_id=tid)
    assert status == 200 and reply.result["confirmed"] is True
    bridge.mcp("task.cancel", request_id=f"{tid}:cancel", task_id=tid)
    status, reply = bridge.mcp("task.confirm", request_id=f"{tid}:confirm:late", task_id=tid)
    assert status == 409 and reply.error.code == "invalid_transition"


def test_annotate_and_metrics(bridge, uid):
    r = bridge.http.post("/annotate", json={"text": f"caption {uid}"})
    assert r.status_code == 200
    steps = [e for e in bridge.events(type="test_step") if e["data"]["text"] == f"caption {uid}"]
    assert len(steps) == 1
    metrics = bridge.get("/metrics")
    for key in (
        "duplicate_requests",
        "duplicate_effects_rejected",
        "stale_writes_blocked",
        "invalid_transitions_rejected",
        "schema_rejections",
        "lease_expiries",
        "recoveries",
        "reclaims",
        "tasks_completed",
        "tasks_failed",
        "tasks_cancelled",
        "avg_task_ms",
        "avg_recovery_ms",
        "faults_applied",
    ):
        assert key in metrics, key


def test_events_survive_a_bridge_restart_without_gaps(tmp_path):
    from tests.conftest import start_bridge, stop_bridge
    from tests.helpers.clients import BridgeClient

    proc, log, url = start_bridge(tmp_path, tmp_path / "bridge.log")
    client = BridgeClient(url)
    flows.create_dialog(client, "persist", [GENERAL])
    before = client.health()
    client.close()
    stop_bridge(proc, log)

    proc, log, url = start_bridge(tmp_path, tmp_path / "bridge.log")
    client = BridgeClient(url)
    try:
        after = client.health()
        assert after["boot_id"] != before["boot_id"]
        assert after["last_seq"] >= before["last_seq"]
        assert [d["msg_id"] for d in client.get("/dialogs")] == ["persist"]
        seqs = [e["seq"] for e in client.events()]
        assert seqs == list(range(1, len(seqs) + 1))
        # Repeating the create after the restart still hits the stored reply.
        flows.create_dialog(client, "persist", [GENERAL])
        assert "duplicate_request" in client.event_types()
    finally:
        client.close()
        stop_bridge(proc, log)


def test_cors_allows_dashboard_origin(bridge):
    r = httpx.options(
        f"{bridge.base_url}/health",
        headers={"Origin": "http://localhost:8000", "Access-Control-Request-Method": "GET"},
    )
    assert r.headers.get("access-control-allow-origin") == "http://localhost:8000"
