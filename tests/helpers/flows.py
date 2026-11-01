"""Small request sequences reused by bridge-level tests (no agents involved).

Request IDs follow docs/IDENTIFIERS.md, so a test reads like what an agent
would send.
"""

from __future__ import annotations

from typing import Any

from tests.helpers.clients import BridgeClient
from tests.helpers.wait import wait_until

GENERAL = {
    "intent": "general",
    "action": None,
    "params": {"text": "write me a poem"},
    "steps": ["parse", "plan", "work", "report"],
    "needs_confirmation": False,
    "depends_on": [],
}
PAYMENT = {
    "intent": "payment",
    "action": "pay",
    "params": {"amount": 40, "currency": "INR", "payee": "bakery"},
    "steps": ["parse", "plan", "pay", "verify", "report"],
    "needs_confirmation": True,
    "depends_on": [],
}
MESSAGE = {
    "intent": "message",
    "action": "send",
    "params": {"to": "Rahul", "body": "receipt"},
    "steps": ["parse", "draft", "send", "report"],
    "needs_confirmation": True,
    "depends_on": [1],
}


def create_dialog(bridge: BridgeClient, msg_id: str, tasks: list[dict], text: str = "test"):
    status, reply = bridge.mcp(
        "dialog.create",
        request_id=f"{msg_id}:create",
        payload={"msg_id": msg_id, "text": text, "tasks": tasks},
    )
    assert status == 200, reply
    return reply.result


def wait_state(bridge: BridgeClient, task_id: str, state: str, timeout: float = 5):
    return wait_until(lambda: (t := bridge.task(task_id))["state"] == state and t, timeout=timeout)


def confirm(bridge: BridgeClient, task_id: str):
    status, reply = bridge.mcp("task.confirm", request_id=f"{task_id}:confirm", task_id=task_id)
    assert status == 200, reply
    return reply.result


def dispatched_task(bridge: BridgeClient, msg_id: str, spec: dict = GENERAL) -> str:
    task_id = create_dialog(bridge, msg_id, [spec])["task_ids"][0]
    if spec["needs_confirmation"]:
        confirm(bridge, task_id)
    wait_state(bridge, task_id, "DISPATCHED")
    return task_id


def claim(bridge: BridgeClient, task_id: str, agent: str = "agent_b", boot: str = "boot1"):
    return bridge.a2a(
        "task.claim", request_id=f"{agent}:claim:{task_id}:{boot}", sender=agent, task_id=task_id
    )


def resume(bridge: BridgeClient, agent: str = "agent_b", boot: str = "boot2"):
    return bridge.a2a("task.resume", request_id=f"{agent}:resume:{boot}", sender=agent)


def worker_write(
    bridge: BridgeClient,
    op: str,
    task: dict[str, Any],
    fence: int,
    payload: dict | None = None,
    agent: str = "agent_b",
    key: str | None = None,
):
    """A worker write with the request_id derived as in IDENTIFIERS.md."""
    tid = task["task_id"]
    suffix = {
        "task.checkpoint": f"checkpoint:{(payload or {}).get('step_index')}:{fence}",
        "task.effect_intent": f"effect_intent:{fence}",
        "task.effect_result": f"effect_result:{fence}",
        "task.complete": f"complete:{fence}",
        "task.fail": f"fail:{fence}",
    }[op]
    return bridge.a2a(
        op,
        request_id=key or f"{tid}:{suffix}",
        sender=agent,
        task_id=tid,
        dialog_id=task["dialog_id"],
        fence=fence,
        payload=payload or {},
    )


def checkpoint(bridge, task, fence, step_index, agent="agent_b", output=None):
    payload = {
        "step_index": step_index,
        "step": task["steps"][step_index],
        "output": output or {},
    }
    return worker_write(bridge, "task.checkpoint", task, fence, payload, agent)


def run_steps(bridge, task, fence, start, stop, agent="agent_b"):
    for i in range(start, stop):
        status, reply = checkpoint(bridge, task, fence, i, agent)
        assert status == 200, reply
