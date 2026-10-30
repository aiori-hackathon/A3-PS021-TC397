"""Small steps shared by the S/X scenarios. Everything goes through public endpoints."""

from __future__ import annotations

from typing import Any

from tests.helpers.wait import wait_until

PAY = "pay ₹40 to the bakery"
PAY_AND_EMAIL = "Pay ₹40 to the bakery and email the receipt to Rahul"


def task_in(world, task_id: str, *states: str, timeout: float = 10) -> dict[str, Any]:
    last: dict[str, Any] = {}

    def check():
        last.update(world.bridge.task(task_id))
        return last["state"] in states and dict(last)

    try:
        return wait_until(check, timeout=timeout)
    except TimeoutError as exc:
        trail = [
            (e["type"], e["actor"], e["data"].get("to") or e["data"].get("attempt"))
            for e in world.bridge.events(task_id=task_id)
        ]
        status = {n: (v["status"], v["crashes"]) for n, v in world.supervisor.status().items()}
        raise TimeoutError(
            f"{task_id} never reached {states}: state={last.get('state')} owner={last.get('owner')}"
            f" fence={last.get('fence')} attempt={last.get('attempt')}\nevents={trail}\n"
            f"processes={status}"
        ) from exc


def process_is(world, name: str, status: str, timeout: float = 10) -> None:
    wait_until(lambda: world.supervisor.state(name) == status, timeout=timeout)


def add_fault(client, **rule) -> dict[str, Any]:
    r = client.http.post("/faults", json=rule)
    r.raise_for_status()
    return r.json()


def events_of(world, task_id: str, type: str | None = None) -> list[dict[str, Any]]:
    return world.bridge.events(task_id=task_id, type=type)


def checkpoint_counts(world, task_id: str) -> dict[int, int]:
    counts: dict[int, int] = {}
    for e in events_of(world, task_id, "checkpoint"):
        counts[e["data"]["step_index"]] = counts.get(e["data"]["step_index"], 0) + 1
    return counts


def chat_has(world, kind: str, dialog_id: str, timeout: float = 10) -> dict[str, Any]:
    return wait_until(
        lambda: next(
            (
                m
                for m in world.agent_a.chat()
                if m["kind"] == kind and m.get("dialog_id") == dialog_id
            ),
            None,
        ),
        timeout=timeout,
    )
