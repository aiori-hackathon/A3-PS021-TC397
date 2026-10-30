"""MCP-style (JSON-RPC 2.0 shaped) codec. See docs/PROTOCOLS.md.

A mock format that borrows the tool-call shape; it makes no conformance
claim. Every bridge op is a `tools/call`; dialog metadata travels in
`params._meta`; the JSON-RPC `id` is only for matching a reply on one
connection and is never the idempotency key (that is `_meta.request_id`).
"""

from __future__ import annotations

from typing import Any

from bridge.codecs import ENVELOPE_ID_FIELDS, CodecError, is_task_record
from bridge.models import Envelope, ErrorBody, Reply, Task

ERROR_CODES: dict[str, int] = {
    "validation_error": -32602,
    "schema_unsupported": -32600,
    "not_found": -32004,
    "invalid_transition": -32001,
    "not_owner": -32001,
    "stale_fence": -32001,
    "already_claimed": -32001,
    "request_id_conflict": -32002,
}
# The human-readable error text rides in error.data under this reserved key;
# the bridge error code is error.message, as in the doc example.
MESSAGE_KEY = "_message"


def _dict(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CodecError(f"{what} must be an object")
    return value


def encode_request(env: Envelope, rpc_id: int | str = 1) -> dict[str, Any]:
    arguments: dict[str, Any] = {}
    for name in ENVELOPE_ID_FIELDS:
        if getattr(env, name) is not None:
            arguments[name] = getattr(env, name)
    clash = set(arguments) & set(env.payload)
    if clash:
        raise CodecError(f"payload may not use reserved argument names: {sorted(clash)}")
    arguments.update(env.payload)
    meta = {"schema": env.schema_, "sender": env.sender, "sent_at": env.sent_at}
    if env.request_id is not None:
        meta["request_id"] = env.request_id
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "method": "tools/call",
        "params": {"name": env.op, "arguments": arguments, "_meta": meta},
    }


def decode_request(native: dict[str, Any]) -> Envelope:
    native = _dict(native, "request")
    if native.get("jsonrpc") != "2.0" or native.get("method") != "tools/call":
        raise CodecError("expected a JSON-RPC 2.0 tools/call request")
    params = _dict(native.get("params"), "params")
    arguments = dict(_dict(params.get("arguments", {}), "params.arguments"))
    meta = _dict(params.get("_meta"), "params._meta")
    ids = {name: arguments.pop(name, None) for name in ENVELOPE_ID_FIELDS}
    return Envelope(
        schema=meta.get("schema"),
        request_id=meta.get("request_id"),
        sender=meta.get("sender"),
        op=params.get("name"),
        payload=arguments,
        sent_at=meta.get("sent_at"),
        **ids,
    )


def _summary(result: Any) -> str:
    if is_task_record(result):
        return f"task {result['task_id']}: {result['state']}"
    if isinstance(result, dict) and "dialog_id" in result and "task_ids" in result:
        return f"dialog {result['dialog_id']} created with {len(result['task_ids'])} tasks"
    if result is None:
        return "no result"
    return "ok"


def encode_reply(reply: Reply, native_request: Any) -> dict[str, Any]:
    rpc_id = native_request.get("id") if isinstance(native_request, dict) else None
    if reply.ok:
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "content": [{"type": "text", "text": _summary(reply.result)}],
                "structuredContent": reply.result,
                "isError": False,
                "_meta": {"event_seq": reply.event_seq},
            },
        }
    err = reply.error
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "error": {
            "code": ERROR_CODES.get(err.code, -32000),
            "message": err.code,
            "data": {**err.details, MESSAGE_KEY: err.message},
        },
    }


def decode_reply(native: dict[str, Any]) -> Reply:
    native = _dict(native, "reply")
    if "error" in native:
        error = _dict(native["error"], "error")
        data = dict(error.get("data") or {})
        message = data.pop(MESSAGE_KEY, error.get("message", ""))
        return Reply(
            ok=False, error=ErrorBody(code=error.get("message"), message=message, details=data)
        )
    result = _dict(native.get("result"), "result")
    return Reply.success(
        result.get("structuredContent"), (result.get("_meta") or {}).get("event_seq")
    )


def encode_task_view(task: Task) -> dict[str, Any]:
    record = task.model_dump(mode="json")
    return {
        "content": [{"type": "text", "text": _summary(record)}],
        "structuredContent": record,  # exact bridge state, returned as data
        "isError": False,
    }


def decode_task_view(native: dict[str, Any]) -> Task:
    return Task.model_validate(_dict(native, "task view").get("structuredContent"))
