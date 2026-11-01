"""I1, I2: codec round trips (docs/PROTOCOLS.md). Pure functions, no servers."""

import json

import pytest

from bridge.codecs import a2a_style, mcp_style
from bridge.models import OPS, SCHEMA_VERSION, STATE_CHANGING_OPS, Envelope, Reply, Task
from bridge.state_machine import STATES

SENT_AT = "2026-11-02T10:15:03.120Z"
TID = "tsk_8c1f0a2b9e44"
DID = "dlg_3fa91c07d2e1"

PAYLOADS = {
    "agent.register": {"protocol": "a2a-style", "base_url": "http://localhost:9002"},
    "agent.heartbeat": {"task_ids": [TID]},
    "dialog.create": {
        "msg_id": "msg_17",
        "text": "Pay ₹40 to the bakery and email the receipt to Rahul",
        "tasks": [
            {
                "intent": "payment",
                "action": "pay",
                "params": {"amount": 40, "currency": "INR", "payee": "bakery"},
                "steps": ["parse", "plan", "pay", "verify", "report"],
                "needs_confirmation": True,
                "depends_on": [],
            },
            {
                "intent": "message",
                "action": "send",
                "params": {"to": "Rahul", "body": "receipt"},
                "steps": ["parse", "draft", "send", "report"],
                "needs_confirmation": True,
                "depends_on": [1],
            },
        ],
    },
    "dialog.get": {},
    "task.confirm": {},
    "task.cancel": {"reason": "user"},
    "task.claim_next": {},
    "task.claim": {},
    "task.resume": {},
    "task.checkpoint": {"step_index": 2, "step": "pay", "output": {"receipt_id": "rcp_51d0"}},
    "task.effect_intent": {"effect_key": f"{TID}:pay", "action": "pay", "params": {"amount": 40}},
    "task.effect_result": {"receipt_id": "rcp_51d0a8c2e911", "replayed": True},
    "task.complete": {"result": {"summary": "paid ₹40"}},
    "task.fail": {"reason": "card declined", "retryable": False},
    "events.since": {"seq": 41},
}


def envelope(op, **kw):
    fields = {
        "schema": SCHEMA_VERSION,
        "request_id": f"{TID}:{op}:1" if op in STATE_CHANGING_OPS else None,
        "sender": "agent_b",
        "op": op,
        "dialog_id": DID,
        "task_id": TID,
        "fence": 1,
        "payload": PAYLOADS[op],
        "sent_at": SENT_AT,
    }
    fields.update(kw)
    return Envelope(**fields)


def all_envelopes():
    for op in OPS:
        yield envelope(op)
        # Same op with every optional field empty.
        yield envelope(op, dialog_id=None, task_id=None, fence=None)


def task(state, **kw):
    fields = {
        "task_id": TID,
        "dialog_id": DID,
        "index": 1,
        "intent": "payment",
        "action": "pay",
        "params": {"amount": 40, "currency": "INR", "payee": "bakery"},
        "steps": ["parse", "plan", "pay", "verify", "report"],
        "state": state,
        "owner": "agent_b",
        "lease_until": "2026-11-02T10:15:09.120Z",
        "fence": 2,
        "attempt": 2,
        "max_attempts": 3,
        "depends_on": [],
        "needs_confirmation": True,
        "confirmed": True,
        "effect": {"key": f"{TID}:pay", "status": "intent", "receipt_id": None, "replayed": False},
        "last_checkpoint": {"step_index": 1, "step": "plan", "output": {}},
        "result": None,
        "reason": None,
        "created_at": "2026-11-02T10:14:58.010Z",
        "updated_at": "2026-11-02T10:15:03.125Z",
    }
    fields.update(kw)
    return Task(**fields)


def wire(obj):
    """Simulate the network: JSON encode and decode."""
    return json.loads(json.dumps(obj))


CODECS = [mcp_style, a2a_style]


# --- I1 / I2: request round trips -------------------------------------------


@pytest.mark.parametrize("codec", CODECS, ids=lambda c: c.__name__.split(".")[-1])
@pytest.mark.parametrize("env", list(all_envelopes()), ids=lambda e: e.op)
def test_request_round_trip(codec, env):
    assert codec.decode_request(wire(codec.encode_request(env))) == env


def test_mcp_field_placement():
    env = envelope("task.checkpoint")
    native = mcp_style.encode_request(env, rpc_id=7)
    assert native["jsonrpc"] == "2.0" and native["id"] == 7
    assert native["method"] == "tools/call"
    params = native["params"]
    assert params["name"] == "task.checkpoint"
    assert params["_meta"] == {
        "schema": SCHEMA_VERSION,
        "request_id": env.request_id,
        "sender": "agent_b",
        "sent_at": SENT_AT,
    }
    assert params["arguments"] == {
        "dialog_id": DID,
        "task_id": TID,
        "fence": 1,
        **PAYLOADS["task.checkpoint"],
    }


def test_a2a_field_placement():
    env = envelope("task.checkpoint")
    msg = a2a_style.encode_request(env)["message"]
    assert msg["messageId"] == env.request_id  # messageId IS the idempotency key
    assert msg["kind"] == "task.checkpoint"
    assert msg["contextId"] == DID and msg["taskId"] == TID
    assert msg["role"] == "agent"
    assert msg["parts"] == [{"kind": "data", "data": PAYLOADS["task.checkpoint"]}]
    assert msg["metadata"] == {
        "schema": SCHEMA_VERSION,
        "sender": "agent_b",
        "fence": 1,
        "sent_at": SENT_AT,
    }


@pytest.mark.parametrize("codec", CODECS, ids=lambda c: c.__name__.split(".")[-1])
@pytest.mark.parametrize("native", [{}, {"foo": 1}, {"message": "x"}, {"params": []}])
def test_malformed_requests_raise(codec, native):
    with pytest.raises(ValueError):
        codec.decode_request(native)


def test_unknown_schema_version_survives_decoding():
    # The route, not the codec, turns this into 400 schema_unsupported.
    env = envelope("task.claim_next", schema="dialog-state/9.9")
    for codec in CODECS:
        assert codec.decode_request(wire(codec.encode_request(env))).schema_ == "dialog-state/9.9"


# --- task views: every state ------------------------------------------------


@pytest.mark.parametrize("codec", CODECS, ids=lambda c: c.__name__.split(".")[-1])
@pytest.mark.parametrize("state", STATES)
def test_task_view_round_trip_every_state(codec, state):
    t = task(state)
    assert codec.decode_task_view(wire(codec.encode_task_view(t))) == t


@pytest.mark.parametrize("codec", CODECS, ids=lambda c: c.__name__.split(".")[-1])
def test_task_view_round_trip_minimal_task(codec):
    t = task(
        "CREATED",
        action=None,
        owner=None,
        lease_until=None,
        fence=0,
        attempt=0,
        effect={"key": None, "status": "none", "receipt_id": None, "replayed": False},
        last_checkpoint=None,
        needs_confirmation=False,
        confirmed=False,
        depends_on=[1],
        index=2,
    )
    assert codec.decode_task_view(wire(codec.encode_task_view(t))) == t


A2A_MAP = {
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


@pytest.mark.parametrize("state", STATES)
def test_a2a_state_mapping(state):
    view = a2a_style.encode_task_view(task(state))
    assert view["status"]["state"] == A2A_MAP[state]
    assert view["metadata"]["bridge_state"] == state  # always present: keeps it lossless
    assert view["id"] == TID and view["contextId"] == DID


def test_i2_suspended_is_carried_in_metadata():
    view = wire(a2a_style.encode_task_view(task("SUSPENDED")))
    assert view["status"]["state"] == "working"
    assert view["metadata"]["bridge_state"] == "SUSPENDED"
    assert a2a_style.decode_task_view(view).state == "SUSPENDED"


def test_a2a_without_bridge_state_falls_back_to_lossy_mapping():
    view = wire(a2a_style.encode_task_view(task("SUSPENDED")))
    del view["metadata"]["bridge_state"]
    assert a2a_style.decode_task_view(view).state == "IN_PROGRESS"


def test_mcp_view_carries_exact_state():
    view = mcp_style.encode_task_view(task("SUSPENDED"))
    assert view["structuredContent"]["state"] == "SUSPENDED"
    assert view["isError"] is False


# --- replies ----------------------------------------------------------------

REPLIES = [
    Reply.success(task("EFFECT_PENDING").model_dump(mode="json"), event_seq=43),
    Reply.success({"tasks": [task("IN_PROGRESS").model_dump(mode="json")]}, event_seq=50),
    Reply.success({"dialog_id": DID, "task_ids": [TID]}, event_seq=12),
    Reply.success(None, event_seq=None),
    Reply.failure("stale_fence", "fence 1 < current 2", {"current_fence": 2}),
    Reply.failure(
        "invalid_transition", "COMPLETED -> IN_PROGRESS", {"from": "COMPLETED", "to": "IN_PROGRESS"}
    ),
    Reply.failure("request_id_conflict", "request_id reused with a different body", {}),
]


@pytest.mark.parametrize("codec", CODECS, ids=lambda c: c.__name__.split(".")[-1])
@pytest.mark.parametrize("reply", REPLIES, ids=lambda r: r.error.code if r.error else "ok")
def test_reply_round_trip(codec, reply):
    native_req = codec.encode_request(envelope("task.checkpoint"))
    assert codec.decode_reply(wire(codec.encode_reply(reply, native_req))) == reply


def test_mcp_reply_shapes():
    req = mcp_style.encode_request(envelope("dialog.create", sender="agent_a"), rpc_id=7)
    ok = mcp_style.encode_reply(Reply.success({"dialog_id": DID, "task_ids": [TID]}, 12), req)
    assert ok["jsonrpc"] == "2.0" and ok["id"] == 7
    assert ok["result"]["isError"] is False
    assert ok["result"]["structuredContent"] == {"dialog_id": DID, "task_ids": [TID]}
    assert ok["result"]["_meta"] == {"event_seq": 12}
    assert ok["result"]["content"][0]["type"] == "text"
    err = mcp_style.encode_reply(
        Reply.failure("invalid_transition", "no", {"from": "COMPLETED", "to": "IN_PROGRESS"}), req
    )
    assert err["id"] == 7
    assert err["error"]["code"] == -32001
    assert err["error"]["message"] == "invalid_transition"
    assert err["error"]["data"]["from"] == "COMPLETED"


@pytest.mark.parametrize(
    "code,rpc",
    [
        ("validation_error", -32602),
        ("schema_unsupported", -32600),
        ("not_found", -32004),
        ("invalid_transition", -32001),
        ("not_owner", -32001),
        ("stale_fence", -32001),
        ("already_claimed", -32001),
        ("request_id_conflict", -32002),
    ],
)
def test_mcp_error_codes(code, rpc):
    assert mcp_style.ERROR_CODES[code] == rpc


def test_a2a_reply_shapes():
    req = a2a_style.encode_request(envelope("task.checkpoint"))
    t = task("EFFECT_PENDING").model_dump(mode="json")
    out = a2a_style.encode_reply(Reply.success(t, event_seq=43), req)
    assert out["task"]["id"] == TID
    assert out["task"]["metadata"]["event_seq"] == 43
    assert out["task"]["metadata"]["bridge_state"] == "EFFECT_PENDING"
    err = a2a_style.encode_reply(Reply.failure("stale_fence", "m", {"current_fence": 2}), req)
    assert err == {
        "error": {"code": "stale_fence", "message": "m", "details": {"current_fence": 2}}
    }


# --- I3 (unit part): same IDs across both views -------------------------------


@pytest.mark.parametrize("state", STATES)
def test_both_views_share_ids_and_agree_on_state(state):
    t = task(state)
    m = mcp_style.encode_task_view(t)["structuredContent"]
    a = a2a_style.encode_task_view(t)
    assert m["task_id"] == a["id"] and m["dialog_id"] == a["contextId"]
    assert m["state"] == a["metadata"]["bridge_state"]


# --- peek: best-effort read of identifying fields from a malformed message ---


@pytest.mark.parametrize("codec", CODECS, ids=lambda c: c.__name__.split(".")[-1])
def test_peek_reads_identity_fields(codec):
    native = wire(codec.encode_request(envelope("task.checkpoint", schema="dialog-state/9.9")))
    assert codec.peek(native) == {
        "schema": "dialog-state/9.9",
        "request_id": f"{TID}:task.checkpoint:1",
        "sender": "agent_b",
        "op": "task.checkpoint",
    }


@pytest.mark.parametrize("codec", CODECS, ids=lambda c: c.__name__.split(".")[-1])
@pytest.mark.parametrize("native", [None, [], {}, {"params": 3}, {"message": {"metadata": 1}}])
def test_peek_never_raises(codec, native):
    assert set(codec.peek(native)) == {"schema", "request_id", "sender", "op"}
