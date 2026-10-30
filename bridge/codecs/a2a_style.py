"""A2A-style (task/message shaped) codec. See docs/PROTOCOLS.md.

A mock format that borrows the task/message shape; it makes no conformance
claim. The dialog is the `contextId`, each operation is a message with a
`kind`, and `messageId` is the idempotency key. The A2A-style state set has
no SUSPENDED, so the exact bridge state always travels in
`metadata.bridge_state` (docs/STATE_MACHINE.md#mapping-to-a2a-style-task-states).
"""

from __future__ import annotations

from typing import Any

from bridge.codecs import CodecError, is_task_record
from bridge.models import Envelope, ErrorBody, Reply, Task

STATE_TO_A2A: dict[str, str] = {
    "CREATED": "submitted",
    "DISPATCHED": "submitted",
    "AWAITING_CONFIRMATION": "input-required",
    "IN_PROGRESS": "working",
    "EFFECT_PENDING": "working",
    "SUSPENDED": "working",
    "COMPLETED": "completed",
    "FAILED": "failed",
    "CANCELLED": "canceled",
}
# Lossy fallback, used only if metadata.bridge_state is missing.
A2A_TO_STATE: dict[str, str] = {
    "submitted": "CREATED",
    "input-required": "AWAITING_CONFIRMATION",
    "working": "IN_PROGRESS",
    "completed": "COMPLETED",
    "failed": "FAILED",
    "canceled": "CANCELLED",
}


def _dict(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CodecError(f"{what} must be an object")
    return value


def encode_request(env: Envelope) -> dict[str, Any]:
    metadata: dict[str, Any] = {"schema": env.schema_, "sender": env.sender}
    if env.fence is not None:
        metadata["fence"] = env.fence
    metadata["sent_at"] = env.sent_at
    message: dict[str, Any] = {
        "messageId": env.request_id,
        "role": "agent",
        "kind": env.op,
        "parts": [{"kind": "data", "data": env.payload}],
        "metadata": metadata,
    }
    if env.dialog_id is not None:
        message["contextId"] = env.dialog_id
    if env.task_id is not None:
        message["taskId"] = env.task_id
    return {"message": message}


def decode_request(native: dict[str, Any]) -> Envelope:
    message = _dict(_dict(native, "request").get("message"), "message")
    metadata = _dict(message.get("metadata"), "message.metadata")
    parts = message.get("parts")
    if not isinstance(parts, list) or not parts or _dict(parts[0], "part").get("kind") != "data":
        raise CodecError("message.parts[0] must be a data part")
    return Envelope(
        schema=metadata.get("schema"),
        request_id=message.get("messageId"),
        sender=metadata.get("sender"),
        op=message.get("kind"),
        dialog_id=message.get("contextId"),
        task_id=message.get("taskId"),
        fence=metadata.get("fence"),
        payload=_dict(parts[0].get("data"), "message.parts[0].data"),
        sent_at=metadata.get("sent_at"),
    )


def encode_task_view(task: Task) -> dict[str, Any]:
    record = task.model_dump(mode="json")
    task_id = record.pop("task_id")
    dialog_id = record.pop("dialog_id")
    state = record.pop("state")
    return {
        "kind": "task",
        "id": task_id,
        "contextId": dialog_id,
        "status": {"state": STATE_TO_A2A[state], "timestamp": record["updated_at"]},
        "metadata": {"bridge_state": state, **record},
    }


def decode_task_view(native: dict[str, Any]) -> Task:
    native = _dict(native, "task view")
    metadata = dict(_dict(native.get("metadata"), "task metadata"))
    metadata.pop("event_seq", None)
    state = metadata.pop("bridge_state", None)
    if state is None:
        state = A2A_TO_STATE[_dict(native.get("status"), "status")["state"]]
    return Task.model_validate(
        {
            "task_id": native.get("id"),
            "dialog_id": native.get("contextId"),
            "state": state,
            **metadata,
        }
    )


def encode_reply(reply: Reply, native_request: Any) -> dict[str, Any]:
    if not reply.ok:
        return {"error": reply.error.model_dump(mode="json")}
    result = reply.result
    if is_task_record(result):
        view = encode_task_view(Task.model_validate(result))
        view["metadata"]["event_seq"] = reply.event_seq
        return {"task": view}
    if (
        isinstance(result, dict)
        and set(result) == {"tasks"}
        and all(map(is_task_record, result["tasks"]))
    ):
        views = [encode_task_view(Task.model_validate(t)) for t in result["tasks"]]
        return {"tasks": views, "event_seq": reply.event_seq}
    return {"result": result, "event_seq": reply.event_seq}


def decode_reply(native: dict[str, Any]) -> Reply:
    native = _dict(native, "reply")
    if "error" in native:
        return Reply(ok=False, error=ErrorBody.model_validate(native["error"]))
    if "task" in native:
        view = _dict(native["task"], "task")
        seq = _dict(view.get("metadata"), "task metadata").get("event_seq")
        return Reply.success(decode_task_view(view).model_dump(mode="json"), seq)
    if "tasks" in native:
        tasks = [decode_task_view(v).model_dump(mode="json") for v in native["tasks"]]
        return Reply.success({"tasks": tasks}, native.get("event_seq"))
    return Reply.success(native.get("result"), native.get("event_seq"))
