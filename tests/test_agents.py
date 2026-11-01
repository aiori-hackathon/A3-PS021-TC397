"""Mock adapter behaviour that the scenarios rely on (docs/ADAPTERS.md)."""

import pytest

from tests.helpers.wait import wait_until


def test_negation_schedules_nothing(world):
    plan = world.agent_a.message("don't pay yet", msg_id="neg")
    assert plan["dialog_id"] is None and plan["tasks"] == []
    assert plan["reply"] == "Nothing was scheduled."
    assert world.bridge.get("/dialogs") == []


def test_lookup_completes_without_side_effects(world):
    plan = world.agent_a.message("find booking HK-204", msg_id="look")
    [tid] = plan["task_ids"]
    task = wait_until(
        lambda: (t := world.bridge.task(tid))["state"] == "COMPLETED" and t, timeout=10
    )
    assert task["action"] is None and task["effect"]["status"] == "none"
    assert world.actions.count() == 0
    search = [
        e
        for e in world.bridge.events(task_id=tid, type="checkpoint")
        if e["data"]["step"] == "search"
    ]
    assert len(search) == 1


def test_msg_ids_are_assigned_when_missing(world):
    first = world.agent_a.message("write me a poem")
    second = world.agent_a.message("write me a story")
    assert (first["msg_id"], second["msg_id"]) == ("msg_1", "msg_2")
    dialogs = {d["msg_id"] for d in world.bridge.get("/dialogs")}
    assert dialogs == {"msg_1", "msg_2"}


def test_plan_reply_shows_detected_intent(world):
    plan = world.agent_a.message("pay ₹40 to the bakery", msg_id="det")
    assert "Detected intent: payment · ₹40 · bakery · scripted mode" in plan["reply"]
    chat = world.agent_a.chat()
    assert [m["kind"] for m in chat[:2]] == ["user", "plan"]


@pytest.mark.world_env(AUTO_CONFIRM="0")
def test_payment_waits_for_confirmation_and_can_be_cancelled(world):
    plan = world.agent_a.message("pay ₹40 to the bakery", msg_id="conf")
    [tid] = plan["task_ids"]
    wait_until(lambda: world.bridge.task(tid)["state"] == "AWAITING_CONFIRMATION", timeout=5)
    assert "Confirm" in plan["reply"]
    cancelled = world.agent_a.message("cancel the payment", msg_id="conf-cancel")
    assert cancelled["command"] == {"type": "cancel", "intent": "payment"}
    assert world.bridge.task(tid)["state"] == "CANCELLED"
    assert world.actions.count() == 0


@pytest.mark.world_env(AUTO_CONFIRM="0")
def test_confirm_through_agent_a(world):
    plan = world.agent_a.message("pay ₹40 to the bakery", msg_id="conf2")
    [tid] = plan["task_ids"]
    wait_until(lambda: world.bridge.task(tid)["state"] == "AWAITING_CONFIRMATION", timeout=5)
    assert world.agent_a.confirm(tid)["ok"] is True
    wait_until(lambda: world.bridge.task(tid)["state"] == "COMPLETED", timeout=10)
    assert world.actions.count(task_id=tid, action="pay") == 1


def test_agent_b_reports_its_state(world):
    state = world.agent_b.state()
    assert state["agent_id"] == "agent_b" and state["paused"] is False
    assert state["boot_id"]
    world.agent_b.pause()
    assert world.agent_b.state()["paused"] is True
    world.agent_b.unpause()
    assert world.agent_b.state()["paused"] is False
