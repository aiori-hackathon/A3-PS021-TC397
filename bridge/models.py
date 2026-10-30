"""Pydantic models for the dialog-state/0.1-experimental schema. See docs/SCHEMA.md.

These models are the source of truth. schema/dialog-state-0.1.json is
generated from them with `python -m bridge.models --export`.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.json_schema import models_json_schema

SCHEMA_VERSION = "dialog-state/0.1-experimental"
SUPPORTED_SCHEMAS = frozenset({SCHEMA_VERSION})

State = Literal[
    "CREATED",
    "AWAITING_CONFIRMATION",
    "DISPATCHED",
    "IN_PROGRESS",
    "EFFECT_PENDING",
    "SUSPENDED",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
]
Op = Literal[
    "agent.register",
    "agent.heartbeat",
    "dialog.create",
    "dialog.get",
    "task.confirm",
    "task.cancel",
    "task.claim_next",
    "task.claim",
    "task.resume",
    "task.checkpoint",
    "task.effect_intent",
    "task.effect_result",
    "task.complete",
    "task.fail",
    "events.since",
]
Action = Literal["pay", "send", "reserve"]
EffectStatus = Literal["none", "intent", "committed"]
EventType = Literal[
    # lifecycle
    "dialog_created",
    "task_created",
    "state_changed",
    "confirmed",
    "cancelled",
    "claimed",
    "resumed",
    "checkpoint",
    "effect_intent",
    "effect_committed",
    "completed",
    "failed",
    # protection
    "duplicate_request",
    "duplicate_rejected",
    "stale_fence_blocked",
    "invalid_transition_rejected",
    "schema_rejected",
    "request_id_conflict",
    # health
    "agent_registered",
    "agent_unresponsive",
    "lease_expired",
    "reclaimed",
    "max_attempts_reached",
    "bridge_started",
    # testing
    "fault_rule_set",
    "fault_applied",
    "test_step",
]
ErrorCode = Literal[
    "schema_unsupported",
    "validation_error",
    "not_found",
    "invalid_transition",
    "not_owner",
    "stale_fence",
    "already_claimed",
    "request_id_conflict",
    "fault_injected",
]

OPS: tuple[str, ...] = get_args(Op)
READ_ONLY_OPS = frozenset({"dialog.get", "events.since"})
STATE_CHANGING_OPS = frozenset(OPS) - READ_ONLY_OPS

ERROR_HTTP_STATUS: dict[str, int] = {
    "schema_unsupported": 400,
    "validation_error": 400,
    "not_found": 404,
    "invalid_transition": 409,
    "not_owner": 409,
    "stale_fence": 409,
    "already_claimed": 409,
    "request_id_conflict": 422,
    "fault_injected": 503,
}


def schema_supported(value: str) -> bool:
    return value in SUPPORTED_SCHEMAS


def _check_iso8601(value: str) -> str:
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"not an ISO-8601 timestamp: {value!r}") from exc
    return value


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- payloads, one per op ----------------------------------------------------


class EmptyPayload(_Strict):
    pass


class RegisterPayload(_Strict):
    protocol: Literal["mcp-style", "a2a-style"]
    base_url: str


class HeartbeatPayload(_Strict):
    task_ids: list[str] = Field(default_factory=list)


class TaskSpec(_Strict):
    """One task as Agent A asks for it in dialog.create."""

    intent: str
    action: Action | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    steps: list[str] = Field(min_length=1)
    needs_confirmation: bool = False
    depends_on: list[int] = Field(
        default_factory=list,
        description="1-based indexes of earlier tasks in the same dialog.create",
    )

    @model_validator(mode="after")
    def _action_is_a_step(self) -> TaskSpec:
        if self.action is not None and self.action not in self.steps:
            raise ValueError(f"action {self.action!r} must be one of the steps")
        return self


class DialogCreatePayload(_Strict):
    msg_id: str = Field(min_length=1)
    text: str
    tasks: list[TaskSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _deps_point_backwards(self) -> DialogCreatePayload:
        for index, spec in enumerate(self.tasks, start=1):
            for dep in spec.depends_on:
                if not 1 <= dep < index:
                    raise ValueError(f"task {index}: depends_on {dep} is not an earlier task")
        return self


class CancelPayload(_Strict):
    reason: str = "user"


class CheckpointPayload(_Strict):
    step_index: int = Field(ge=0)
    step: str
    output: dict[str, Any] = Field(default_factory=dict)


class EffectIntentPayload(_Strict):
    effect_key: str
    action: Action
    params: dict[str, Any] = Field(default_factory=dict)


class EffectResultPayload(_Strict):
    receipt_id: str
    replayed: bool = False


class CompletePayload(_Strict):
    result: Any = None


class FailPayload(_Strict):
    reason: str
    retryable: bool = False


class EventsSincePayload(_Strict):
    seq: int = Field(default=0, ge=0)


PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    "agent.register": RegisterPayload,
    "agent.heartbeat": HeartbeatPayload,
    "dialog.create": DialogCreatePayload,
    "dialog.get": EmptyPayload,
    "task.confirm": EmptyPayload,
    "task.cancel": CancelPayload,
    "task.claim_next": EmptyPayload,
    "task.claim": EmptyPayload,
    "task.resume": EmptyPayload,
    "task.checkpoint": CheckpointPayload,
    "task.effect_intent": EffectIntentPayload,
    "task.effect_result": EffectResultPayload,
    "task.complete": CompletePayload,
    "task.fail": FailPayload,
    "events.since": EventsSincePayload,
}


# --- envelope ----------------------------------------------------------------


class Envelope(BaseModel):
    """Every request inside the bridge, after a codec has translated it."""

    model_config = ConfigDict(
        extra="forbid",
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
    )

    # Named schema_ in Python because BaseModel already has a `schema` attribute.
    schema_: str = Field(alias="schema")
    request_id: str | None = None
    sender: str = Field(min_length=1)
    op: Op
    dialog_id: str | None = None
    task_id: str | None = None
    fence: int | None = None
    payload: dict[str, Any]
    sent_at: str

    @field_validator("sent_at")
    @classmethod
    def _sent_at_is_iso(cls, value: str) -> str:
        return _check_iso8601(value)

    @model_validator(mode="after")
    def _op_rules(self) -> Envelope:
        if self.op in STATE_CHANGING_OPS and not self.request_id:
            raise ValueError(f"{self.op} changes state and needs a request_id")
        try:
            PAYLOAD_MODELS[self.op].model_validate(self.payload)
        except ValueError as exc:  # pydantic.ValidationError is a ValueError
            raise ValueError(f"payload for {self.op}: {exc}") from exc
        return self

    def typed_payload(self) -> BaseModel:
        return PAYLOAD_MODELS[self.op].model_validate(self.payload)


# --- records -----------------------------------------------------------------


class Dialog(_Strict):
    dialog_id: str
    msg_id: str
    text: str
    created_by: str
    task_ids: list[str]
    created_at: str


class Effect(_Strict):
    key: str | None = None
    status: EffectStatus = "none"
    receipt_id: str | None = None
    replayed: bool = False


class Checkpoint(_Strict):
    step_index: int
    step: str
    output: dict[str, Any] = Field(default_factory=dict)


class Task(_Strict):
    task_id: str
    dialog_id: str
    index: int
    intent: str
    action: Action | None
    params: dict[str, Any]
    steps: list[str]
    state: State
    owner: str | None
    lease_until: str | None
    fence: int
    attempt: int
    max_attempts: int
    depends_on: list[int]
    needs_confirmation: bool
    confirmed: bool
    effect: Effect
    last_checkpoint: Checkpoint | None
    result: Any = None
    reason: str | None = None
    created_at: str
    updated_at: str


class Event(_Strict):
    seq: int
    ts: str
    dialog_id: str | None
    task_id: str | None
    type: EventType
    actor: str
    data: dict[str, Any] = Field(default_factory=dict)


# --- replies -----------------------------------------------------------------


class ErrorBody(_Strict):
    code: ErrorCode
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class Reply(_Strict):
    ok: bool
    result: Any = None
    event_seq: int | None = None
    error: ErrorBody | None = None

    @classmethod
    def success(cls, result: Any, event_seq: int | None = None) -> Reply:
        return cls(ok=True, result=result, event_seq=event_seq)

    @classmethod
    def failure(cls, code: str, message: str, details: dict[str, Any] | None = None) -> Reply:
        return cls(ok=False, error=ErrorBody(code=code, message=message, details=details or {}))

    @property
    def status_code(self) -> int:
        return 200 if self.ok else ERROR_HTTP_STATUS[self.error.code]

    def to_wire(self) -> dict[str, Any]:
        if self.ok:
            return {"ok": True, "result": self.result, "event_seq": self.event_seq}
        return {"ok": False, "error": self.error.model_dump(mode="json")}

    @classmethod
    def from_wire(cls, data: dict[str, Any]) -> Reply:
        if data.get("ok"):
            return cls.success(data.get("result"), data.get("event_seq"))
        return cls(ok=False, error=ErrorBody.model_validate(data["error"]))


# --- JSON Schema export ------------------------------------------------------

EXPORTED_MODELS: list[type[BaseModel]] = [
    Envelope,
    *dict.fromkeys(PAYLOAD_MODELS.values()),
    Dialog,
    Task,
    Event,
    Reply,
]
SCHEMA_FILE = Path(__file__).resolve().parents[1] / "schema" / "dialog-state-0.1.json"


def json_schema() -> dict[str, Any]:
    _, top = models_json_schema(
        [(m, "validation") for m in EXPORTED_MODELS],
        title=SCHEMA_VERSION,
        description=(
            "Experimental schema for the PS-021 Dialog Bridge prototype. "
            "Generated from bridge/models.py; do not edit by hand."
        ),
    )
    return json.loads(json.dumps(top))


def export(path: Path = SCHEMA_FILE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_schema(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


if __name__ == "__main__":
    if "--export" in sys.argv:
        print(f"wrote {export()}")
    else:
        print("usage: python -m bridge.models --export")
