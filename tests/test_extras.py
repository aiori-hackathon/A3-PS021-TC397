"""X1-X7: additional scenarios (docs/TESTING.md#x1--uncertain-side-effect-outcome)."""

import httpx
import pytest

from tests.helpers.scenario import (
    PAY,
    PAY_AND_EMAIL,
    add_fault,
    chat_has,
    checkpoint_counts,
    events_of,
    process_is,
    task_in,
)
from tests.helpers.wait import wait_until

pytestmark = pytest.mark.extra


def test_x1_uncertain_side_effect_outcome(world):
    world.caption("Setting trap: the actions service loses its reply to the payment once")
    add_fault(
        world.actions,
        link="actions",
        action="drop_reply",
        match={"action": "pay"},
        note="X1: payment happened, reply lost",
    )
    plan = world.agent_a.message(PAY, msg_id="x1")
    [task_id] = plan["task_ids"]
    done = task_in(world, task_id, "COMPLETED")
    assert world.actions.count(task_id=task_id, action="pay") == 1
    assert done["effect"]["replayed"] is True
    assert "duplicate_rejected" in [e["type"] for e in events_of(world, task_id)]
    assert world.supervisor.state("agent_b") == "running"  # it retried, it did not crash
    assert done["fence"] == 1  # same owner throughout
    effects = world.actions.count(task_id=task_id, action="pay")
    world.record(side_effects=effects, key_number=f"effects: {effects}, receipt replayed")


@pytest.mark.world_env(STEP_DELAY="0.5")
def test_x2_slow_not_dead_worker(world, settings):
    plan = world.agent_a.message(PAY, msg_id="x2")
    [task_id] = plan["task_ids"]
    task_in(world, task_id, "IN_PROGRESS")

    world.caption("Agent B becomes slow: every request it sends is delayed by 3 leases")
    add_fault(
        world.bridge,
        link="agent_b",
        action="delay",
        delay_ms=int(3 * settings.lease_seconds * 1000),
        count=-1,
        note="X2: slow, not dead",
    )
    world.supervisor.start("agent_b2")

    world.caption("Its lease expires; Agent B2 reclaims the task with a higher fence")
    done = task_in(world, task_id, "COMPLETED", timeout=15)
    assert done["owner"] == "agent_b2" and done["fence"] == 2
    events = events_of(world, task_id)
    types = [e["type"] for e in events]
    assert "lease_expired" in types
    [reclaimed] = [e for e in events if e["type"] == "reclaimed"]
    assert reclaimed["actor"] == "agent_b2" and reclaimed["data"]["fence"] == 2

    world.caption("Agent B's late writes arrive with fence 1 and are blocked")
    wait_until(
        lambda: any(
            e["actor"] == "agent_b" for e in events_of(world, task_id, "stale_fence_blocked")
        ),
        timeout=10,
    )
    assert world.actions.count(task_id=task_id, action="pay") == 1
    blocked = len(
        [e for e in events_of(world, task_id, "stale_fence_blocked") if e["actor"] == "agent_b"]
    )
    world.record(stale_writes_blocked=blocked, key_number=f"stale writes blocked: {blocked}")


def test_x3_orchestrator_crash_after_create(world):
    world.caption("Setting trap: Agent A crashes right after the bridge created the tasks")
    world.supervisor.restart("agent_a", env={"CRASH_AT": "after_create"})
    with pytest.raises(httpx.HTTPError):
        world.agent_a.message(PAY_AND_EMAIL, msg_id="x3")
    process_is(world, "agent_a", "crashed")

    world.caption("Restarting Agent A: it retries dialog.create with the same request_id")
    world.supervisor.restart("agent_a")
    wait_until(
        lambda: any(
            e["data"]["request_id"] == "x3:create"
            for e in world.bridge.events(type="duplicate_request")
        ),
        timeout=10,
    )
    dialogs = [d for d in world.bridge.get("/dialogs") if d["msg_id"] == "x3"]
    assert len(dialogs) == 1
    assert len(dialogs[0]["task_ids"]) == 2
    assert world.bridge.get("/metrics")["tasks_total"] == 2
    for task_id in dialogs[0]["task_ids"]:
        task_in(world, task_id, "COMPLETED", timeout=10)
    assert world.actions.count(action="pay") == 1 and world.actions.count(action="send") == 1
    created = len(dialogs[0]["task_ids"])
    world.record(tasks_created=created, key_number=f"tasks created: {created}")


def retry_limit_scenario(world, msg_id):
    world.caption("Setting trap: Agent B crashes before paying, every time; auto-restart is on")
    world.supervisor.restart("agent_b", env={"CRASH_AT": "before_pay", "CRASH_MODE": "always"})
    plan = world.agent_a.message(PAY_AND_EMAIL, msg_id=msg_id)
    pay_id, mail_id = plan["task_ids"]
    failed = task_in(world, pay_id, "FAILED", timeout=16)
    mail = task_in(world, mail_id, "CANCELLED", timeout=5)
    return plan, failed, mail


@pytest.mark.world_env(AUTO_RESTART="1")
def test_x4_retry_limit(world):
    plan, failed, mail = retry_limit_scenario(world, "x4")
    assert failed["reason"] == "max_attempts" and failed["attempt"] == 3
    assert mail["reason"] == "dependency_failed"
    assert world.actions.count() == 0
    types = [e["type"] for e in events_of(world, failed["task_id"])]
    assert types.count("lease_expired") >= 1 and "max_attempts_reached" in types
    world.caption("Agent A tells the user")
    chat_has(world, "summary", plan["dialog_id"])
    outcomes = [m["text"] for m in world.agent_a.chat() if m["kind"] == "outcome"]
    assert any("failed" in text for text in outcomes)
    world.record(attempts=failed["attempt"], key_number=f"attempts: {failed['attempt']}")


@pytest.mark.world_env(AUTO_RESTART="1")
def test_x6_dependency_fails(world):
    _, failed, mail = retry_limit_scenario(world, "x6")
    types = [e["type"] for e in events_of(world, mail["task_id"])]
    assert "claimed" not in types  # the dependent task never started
    assert "DISPATCHED" not in [
        e["data"]["to"] for e in events_of(world, mail["task_id"], "state_changed")
    ]
    assert failed["state"] == "FAILED" and mail["state"] == "CANCELLED"
    claims = types.count("claimed")
    world.record(dependent_claims=claims, key_number=f"dependent claims: {claims}")


@pytest.mark.world_env(AUTO_CONFIRM="0")
def test_x5_cancel_before_side_effect(world):
    plan = world.agent_a.message(PAY, msg_id="x5")
    [task_id] = plan["task_ids"]
    task_in(world, task_id, "AWAITING_CONFIRMATION")
    world.caption("The user cancels before confirming")
    assert world.agent_a.cancel(task_id)["ok"] is True
    assert world.bridge.task(task_id)["state"] == "CANCELLED"
    assert world.actions.count() == 0

    world.caption("A task whose payment already happened can never be cancelled")
    world.supervisor.restart("agent_b", env={"CRASH_AT": "after_pay"})
    plan2 = world.agent_a.message(PAY, msg_id="x5b")
    [paid_id] = plan2["task_ids"]
    task_in(world, paid_id, "AWAITING_CONFIRMATION")
    assert world.agent_a.confirm(paid_id)["ok"] is True
    process_is(world, "agent_b", "crashed")
    assert world.bridge.task(paid_id)["effect"]["status"] == "committed"
    refused = world.agent_a.cancel(paid_id)
    assert refused["ok"] is False and refused["error"]["code"] == "invalid_transition"
    world.supervisor.restart("agent_b")
    task_in(world, paid_id, "COMPLETED")
    assert world.actions.count(task_id=paid_id, action="pay") == 1
    rows = world.actions.count(task_id=task_id)
    world.record(ledger_rows=rows, key_number=f"ledger rows: {rows}; committed cancel refused")


def test_x7_duplicate_dispatch_to_worker(world):
    world.caption("Setting trap: one checkpoint from Agent B is delivered twice")
    rule = add_fault(
        world.bridge,
        link="agent_b",
        action="duplicate",
        match={"op": "task.checkpoint"},
        note="X7: duplicate delivery",
    )
    plan = world.agent_a.message(PAY, msg_id="x7")
    [task_id] = plan["task_ids"]
    task_in(world, task_id, "COMPLETED")
    assert checkpoint_counts(world, task_id) == dict.fromkeys(range(5), 1)
    duplicates = [
        e
        for e in events_of(world, task_id, "duplicate_request")
        if e["data"]["op"] == "task.checkpoint"
    ]
    assert len(duplicates) == 1
    applied = [
        e for e in world.bridge.events(type="fault_applied") if e["data"]["rule_id"] == rule["id"]
    ]
    assert len(applied) == 1
    assert world.actions.count(task_id=task_id, action="pay") == 1
    world.record(key_number="1 checkpoint row per step; duplicate absorbed")
