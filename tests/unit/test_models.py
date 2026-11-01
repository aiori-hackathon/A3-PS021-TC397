"""Schema models: envelope validation and the generated JSON Schema file."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from bridge import models
from bridge.models import (
    OPS,
    SCHEMA_VERSION,
    STATE_CHANGING_OPS,
    Envelope,
    Reply,
)

SENT_AT = "2026-11-02T10:15:03.120Z"


def make(op="task.checkpoint", **kw):
    base = {
        "schema": SCHEMA_VERSION,
        "request_id": "tsk_8c1f0a2b9e44:checkpoint:2:1",
        "sender": "agent_b",
        "op": op,
        "dialog_id": "dlg_3fa91c07d2e1",
        "task_id": "tsk_8c1f0a2b9e44",
        "fence": 1,
        "payload": {"step_index": 2, "step": "pay", "output": {"receipt_id": "rcp_51d0"}},
        "sent_at": SENT_AT,
    }
    base.update(kw)
    return Envelope(**base)


def test_schema_label():
    assert SCHEMA_VERSION == "dialog-state/0.1-experimental"


def test_envelope_dumps_schema_field_by_its_wire_name():
    data = make().model_dump(mode="json")
    assert data["schema"] == SCHEMA_VERSION
    assert "schema_" not in data


def test_state_changing_op_requires_request_id():
    with pytest.raises(ValidationError):
        make(request_id=None)


def test_read_only_ops_need_no_request_id():
    env = make(op="events.since", request_id=None, fence=None, payload={"seq": 3})
    assert env.request_id is None


def test_unknown_op_rejected():
    with pytest.raises(ValidationError):
        make(op="task.teleport")


def test_payload_checked_against_op():
    with pytest.raises(ValidationError):
        make(payload={"step": "pay", "output": {}})  # step_index missing
    with pytest.raises(ValidationError):
        make(payload={"step_index": 2, "step": "pay", "output": {}, "extra": 1})


def test_schema_value_is_not_judged_by_the_model():
    # Unsupported versions are a 400 schema_unsupported decided by the route,
    # not a validation_error, so the model accepts any string here.
    env = make(schema="dialog-state/9.9")
    assert env.schema_ == "dialog-state/9.9"
    assert not models.schema_supported(env.schema_)
    assert models.schema_supported(SCHEMA_VERSION)


def test_sent_at_must_be_iso8601():
    with pytest.raises(ValidationError):
        make(sent_at="yesterday")


def test_dialog_create_payload():
    env = make(
        op="dialog.create",
        request_id="msg_17:create",
        sender="agent_a",
        dialog_id=None,
        task_id=None,
        fence=None,
        payload={
            "msg_id": "msg_17",
            "text": "pay",
            "tasks": [
                {
                    "intent": "payment",
                    "action": "pay",
                    "params": {"amount": 40},
                    "steps": ["parse", "plan", "pay", "verify", "report"],
                    "needs_confirmation": True,
                    "depends_on": [],
                }
            ],
        },
    )
    assert env.payload["tasks"][0]["intent"] == "payment"


def test_dialog_create_depends_on_must_point_to_earlier_tasks():
    spec = {"intent": "general", "action": None, "params": {}, "steps": ["parse"]}
    with pytest.raises(ValidationError):
        make(
            op="dialog.create",
            request_id="m:create",
            payload={"msg_id": "m", "text": "x", "tasks": [{**spec, "depends_on": [1]}]},
        )


def test_every_op_has_a_payload_model_and_state_flag():
    assert set(models.PAYLOAD_MODELS) == set(OPS)
    assert STATE_CHANGING_OPS == set(OPS) - {"dialog.get", "events.since"}


def test_reply_wire_shapes_and_status():
    ok = Reply.success({"task_id": "t"}, event_seq=42)
    assert ok.to_wire() == {"ok": True, "result": {"task_id": "t"}, "event_seq": 42}
    assert ok.status_code == 200
    err = Reply.failure("stale_fence", "fence 1 < current 2", {"current_fence": 2})
    assert err.to_wire() == {
        "ok": False,
        "error": {
            "code": "stale_fence",
            "message": "fence 1 < current 2",
            "details": {"current_fence": 2},
        },
    }
    assert err.status_code == 409
    assert Reply.from_wire(err.to_wire()) == err
    assert Reply.from_wire(ok.to_wire()) == ok


@pytest.mark.parametrize(
    "code,status",
    [
        ("schema_unsupported", 400),
        ("validation_error", 400),
        ("not_found", 404),
        ("invalid_transition", 409),
        ("not_owner", 409),
        ("stale_fence", 409),
        ("already_claimed", 409),
        ("request_id_conflict", 422),
        ("fault_injected", 503),
    ],
)
def test_error_http_status(code, status):
    assert models.ERROR_HTTP_STATUS[code] == status


def test_exported_json_schema_is_up_to_date():
    path = Path(__file__).resolve().parents[2] / "schema" / "dialog-state-0.1.json"
    assert path.exists(), "run: python -m bridge.models --export"
    on_disk = json.loads(path.read_text())
    assert on_disk == models.json_schema(), "stale: run python -m bridge.models --export"
    assert on_disk["title"] == SCHEMA_VERSION
