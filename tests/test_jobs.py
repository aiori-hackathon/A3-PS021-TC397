"""Bridge background jobs: lease watchdog, agent health, startup recovery.

Bare bridge processes (no agents), so nothing heartbeats unless the test does.
"""

import time

from tests.conftest import start_bridge, stop_process
from tests.helpers import flows
from tests.helpers.assertions import assert_events_in_order
from tests.helpers.clients import BridgeClient
from tests.helpers.flows import GENERAL
from tests.helpers.wait import wait_until


def test_lease_expiry_suspends_then_fails_at_the_limit(fresh_bridge):
    bridge = fresh_bridge
    t1, t2 = flows.create_dialog(bridge, "x4ish", [GENERAL, {**GENERAL, "depends_on": [1]}])[
        "task_ids"
    ]
    flows.wait_state(bridge, t1, "DISPATCHED")
    owners = ["agent_b", "agent_b2", "agent_b"]
    for attempt, agent in enumerate(owners, start=1):
        status, reply = flows.claim(bridge, t1, agent, boot=f"boot{attempt}")
        assert status == 200, reply
        assert reply.result["attempt"] == attempt
        if attempt < 3:
            flows.wait_state(bridge, t1, "SUSPENDED", timeout=6)
    failed = flows.wait_state(bridge, t1, "FAILED", timeout=6)
    assert failed["reason"] == "max_attempts" and failed["attempt"] == 3

    events = bridge.events(task_id=t1)
    types = [e["type"] for e in events]
    assert types.count("lease_expired") == 3
    assert_events_in_order(
        events,
        ["claimed", "lease_expired", "reclaimed", "lease_expired", "reclaimed", "lease_expired"]
        + ["max_attempts_reached", "failed"],
    )
    moves = [(e["data"]["from"], e["data"]["to"]) for e in events if e["type"] == "state_changed"]
    assert moves[-2:] == [("IN_PROGRESS", "SUSPENDED"), ("SUSPENDED", "FAILED")]

    dependent = flows.wait_state(bridge, t2, "CANCELLED")
    assert dependent["reason"] == "dependency_failed"
    assert "claimed" not in bridge.event_types(task_id=t2)


def test_heartbeats_keep_the_lease_alive(fresh_bridge, settings):
    bridge = fresh_bridge
    tid = flows.dispatched_task(bridge, "hb")
    flows.claim(bridge, tid)
    beats = iter(range(1, 1000))
    until = time.monotonic() + settings.lease_seconds * 1.5

    def beat_until_past_one_lease():
        bridge.a2a(
            "agent.heartbeat",
            request_id=f"agent_b:hb:boot1:{next(beats)}",
            payload={"task_ids": [tid]},
        )
        return time.monotonic() > until

    wait_until(beat_until_past_one_lease, timeout=10, every=settings.heartbeat_seconds)
    assert bridge.task(tid)["state"] == "IN_PROGRESS"
    assert "lease_expired" not in bridge.event_types(task_id=tid)


def test_silent_agent_becomes_unresponsive(fresh_bridge):
    bridge = fresh_bridge
    bridge.a2a(
        "agent.register",
        request_id="agent_q:register:boot1",
        sender="agent_q",
        payload={"protocol": "a2a-style", "base_url": "http://127.0.0.1:1"},
    )
    wait_until(
        lambda: (
            {a["agent_id"]: a for a in bridge.get("/agents")}["agent_q"]["status"] == "unresponsive"
        ),
        timeout=5,
    )
    assert "agent_unresponsive" in bridge.event_types()
    bridge.a2a("agent.heartbeat", request_id="agent_q:hb:boot1:1", sender="agent_q")
    agents = {a["agent_id"]: a for a in bridge.get("/agents")}
    assert agents["agent_q"]["status"] == "online"


def test_startup_recovery_after_a_hard_kill(tmp_path):
    proc, log, url = start_bridge(tmp_path, tmp_path / "bridge.log")
    bridge = BridgeClient(url)
    bridge.a2a(
        "agent.register",
        request_id="agent_b:register:boot1",
        payload={"protocol": "a2a-style", "base_url": "http://127.0.0.1:1"},
    )
    tid = flows.dispatched_task(bridge, "boot")
    flows.claim(bridge, tid)
    before = bridge.health()
    bridge.close()
    proc.kill()  # no cleanup, like a crash
    proc.wait()
    log.close()

    proc, log, url = start_bridge(tmp_path, tmp_path / "bridge.log")
    bridge = BridgeClient(url)
    try:
        started = bridge.events(type="bridge_started")
        assert len(started) == 2  # one per boot
        last = started[-1]
        assert last["seq"] == before["last_seq"] + 1  # the sequence continues, no gap
        assert last["data"]["previous_last_seq"] == before["last_seq"]
        assert last["data"]["in_flight_tasks"] == 1
        assert last["data"]["recovered_tasks"] == 1
        assert last["data"]["recovered_dialogs"] == 1
        assert last["data"]["boot_id"] == bridge.health()["boot_id"] != before["boot_id"]
        # The in-flight task kept its identity and got a grace period, not a suspension.
        task = bridge.task(tid)
        assert (task["task_id"], task["state"], task["fence"]) == (tid, "IN_PROGRESS", 1)
        assert task["lease_until"] >= last["ts"]
        assert [a["status"] for a in bridge.get("/agents")] == ["unknown"]
    finally:
        bridge.close()
        stop_process(proc, log)
