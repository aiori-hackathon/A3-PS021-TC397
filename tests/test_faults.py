"""Fault injection on the bridge and actions links (docs/FAULT_INJECTION.md).

Bare processes, so the only traffic is the test's own.
"""

import time

from tests.helpers import flows
from tests.helpers.flows import GENERAL


def add_rule(client, **rule):
    r = client.http.post("/faults", json=rule)
    assert r.status_code == 200, r.text
    return r.json()


def create_raw(bridge, msg_id):
    return bridge.send(
        "mcp",
        "dialog.create",
        request_id=f"{msg_id}:create",
        payload={"msg_id": msg_id, "text": "x", "tasks": [GENERAL]},
    )


def test_drop_request_never_reaches_the_handler(fresh_bridge):
    bridge = fresh_bridge
    rule = add_rule(bridge, link="agent_a", action="drop_request", note="lose it")
    r = create_raw(bridge, "dr")
    assert r.status_code == 503 and r.headers["X-Fault"] == "dropped"
    assert bridge.get("/dialogs") == []
    applied = bridge.events(type="fault_applied")
    assert [e["data"]["rule_id"] for e in applied] == [rule["id"]]
    assert applied[0]["data"]["note"] == "lose it"
    assert bridge.get("/faults") == []  # count 1: used up


def test_drop_reply_commits_then_the_retry_gets_the_stored_reply(fresh_bridge):
    bridge = fresh_bridge
    add_rule(bridge, link="agent_a", action="drop_reply", match={"op": "dialog.create"})
    r = create_raw(bridge, "s1ish")
    assert r.status_code == 503 and r.headers["X-Fault"] == "reply-dropped"
    assert [d["msg_id"] for d in bridge.get("/dialogs")] == ["s1ish"]  # it did commit
    retry = create_raw(bridge, "s1ish")
    assert retry.status_code == 200
    assert len(bridge.get("/dialogs")) == 1
    assert "duplicate_request" in bridge.event_types()


def test_match_filters_by_op(fresh_bridge):
    bridge = fresh_bridge
    add_rule(bridge, link="agent_a", action="drop_request", match={"op": "task.confirm"})
    assert create_raw(bridge, "m1").status_code == 200
    assert len(bridge.get("/faults")) == 1  # not consumed by another op


def test_a2a_link_is_chosen_by_sender(fresh_bridge):
    bridge = fresh_bridge
    add_rule(bridge, link="agent_b2", action="drop_request", count=-1)
    ok = bridge.send("a2a", "task.claim_next", request_id="agent_b:claim_next:x:1")
    blocked = bridge.send(
        "a2a", "task.claim_next", request_id="agent_b2:claim_next:x:1", sender="agent_b2"
    )
    assert ok.status_code == 200 and blocked.status_code == 503


def test_cut_drops_everything_until_deleted(fresh_bridge):
    bridge = fresh_bridge
    rule = add_rule(bridge, link="agent_a", action="cut", count=-1)
    for _ in range(3):
        r = bridge.send("mcp", "events.since", payload={"seq": 0})
        assert r.status_code == 503 and r.headers["X-Fault"] == "link-cut"
    assert bridge.http.delete(f"/faults/{rule['id']}").status_code == 200
    assert bridge.send("mcp", "events.since", payload={"seq": 0}).status_code == 200


def test_duplicate_runs_the_handler_twice_and_the_inbox_absorbs_it(fresh_bridge):
    bridge = fresh_bridge
    add_rule(bridge, link="agent_a", action="duplicate", match={"op": "dialog.create"})
    r = create_raw(bridge, "dup")
    assert r.status_code == 200
    assert len(bridge.get("/dialogs")) == 1
    assert bridge.event_types().count("duplicate_request") == 1


def test_delay_slows_the_reply(fresh_bridge):
    bridge = fresh_bridge
    add_rule(bridge, link="agent_a", action="delay", delay_ms=400)
    start = time.monotonic()
    r = bridge.send("mcp", "events.since", payload={"seq": 0})
    assert r.status_code == 200
    assert time.monotonic() - start >= 0.4


def test_rule_set_events_list_and_clear(fresh_bridge):
    bridge = fresh_bridge
    add_rule(bridge, link="agent_a", action="cut", count=-1, note="demo cut")
    add_rule(bridge, link="agent_b", action="delay", delay_ms=10, count=2)
    rules = bridge.get("/faults")
    assert [(r["link"], r["count"]) for r in rules] == [("agent_a", -1), ("agent_b", 2)]
    assert bridge.event_types(type="fault_rule_set") == ["fault_rule_set"] * 2
    assert bridge.http.delete("/faults").json()["deleted"] == 2
    assert bridge.get("/faults") == []
    assert bridge.http.post("/faults", json={"link": "actions", "action": "cut"}).status_code == 400
    assert bridge.http.delete("/faults/r999").status_code == 404


def test_read_only_routes_are_never_faulted(fresh_bridge):
    bridge = fresh_bridge
    add_rule(bridge, link="agent_a", action="cut", count=-1)
    assert bridge.health()["ok"] is True
    assert bridge.get("/dialogs") == []


def test_actions_drop_reply_still_performs_the_effect(actions_service):
    a = actions_service
    add_rule(a, link="actions", action="drop_reply", match={"action": "pay"})
    lost = a.perform("tsk_x:pay", "tsk_x", 1, "pay", {"amount": 40})
    assert lost.status_code == 503 and lost.headers["X-Fault"] == "reply-dropped"
    assert a.count(task_id="tsk_x") == 1  # it happened; only the reply was lost
    retry = a.perform("tsk_x:pay", "tsk_x", 1, "pay", {"amount": 40})
    assert retry.status_code == 200 and retry.json()["replayed"] is True
    assert a.count(task_id="tsk_x") == 1


def test_actions_cut_and_link_validation(actions_service):
    a = actions_service
    rule = add_rule(a, link="actions", action="cut", count=-1)
    assert a.perform("k", "t", 1, "send").status_code == 503
    assert a.count() == 0
    assert a.http.get("/faults").json()[0]["id"] == rule["id"]
    a.http.delete("/faults")
    assert a.perform("k", "t", 1, "send").status_code == 200
    assert a.http.post("/faults", json={"link": "agent_a", "action": "cut"}).status_code == 400


def test_flows_helper_still_works_with_no_rules(fresh_bridge):
    assert flows.dispatched_task(fresh_bridge, "plain").startswith("tsk_")
