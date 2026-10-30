"""The request path inside the bridge (docs/ARCHITECTURE.md#inside-the-bridge).

codec decode -> schema check -> inbox -> state machine + leases -> one
transaction (state change + inbox reply + events) -> codec encode.

Steps 4-7 run inside one BEGIN IMMEDIATE transaction. The op itself runs in a
savepoint: if it refuses the request, its partial writes are undone, the
refusal event is logged, and the error reply is stored like any other reply.
"""

from __future__ import annotations

from collections.abc import Callable
from types import ModuleType
from typing import Any

from pydantic import ValidationError

from bridge import events, inbox, leases, registry, tasks
from bridge import state_machine as sm
from bridge.db import Database, Tx
from bridge.errors import BridgeError
from bridge.models import (
    READ_ONLY_OPS,
    SCHEMA_VERSION,
    Envelope,
    EventsSincePayload,
    RegisterPayload,
    Reply,
    schema_supported,
)
from config import Settings

EVENTS_PAGE = 1000

# Error codes that leave a "protection" event in the log.
REFUSAL_EVENTS = {
    "invalid_transition": "invalid_transition_rejected",
    "stale_fence": "stale_fence_blocked",
}


def _validation_reply(exc: Exception) -> Reply:
    if isinstance(exc, ValidationError):
        errors = [
            {"loc": [str(p) for p in e["loc"]], "msg": e["msg"]}
            for e in exc.errors(include_url=False)
        ]
        return Reply.failure(
            "validation_error", "request does not match the schema", {"errors": errors}
        )
    return Reply.failure("validation_error", str(exc))


class Bridge:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        self.ops: dict[str, Callable[[Tx, Envelope, str], Any]] = {
            "agent.register": self.op_register,
            "agent.heartbeat": self.op_heartbeat,
            "dialog.create": self.op_dialog_create,
            "dialog.get": self.op_dialog_get,
            "task.confirm": lambda tx, env, _: tasks.confirm(tx, env),
            "task.cancel": lambda tx, env, _: tasks.cancel(tx, env),
            "task.claim_next": self.op_claim_next,
            "task.claim": self.op_claim,
            "task.resume": self.op_resume,
            "task.checkpoint": lambda tx, env, _: tasks.checkpoint(tx, env),
            "task.effect_intent": lambda tx, env, _: tasks.effect_intent(tx, env),
            "task.effect_result": lambda tx, env, _: tasks.effect_result(tx, env),
            "task.complete": lambda tx, env, _: tasks.complete(tx, env),
            "task.fail": lambda tx, env, _: tasks.fail(tx, env),
            "events.since": self.op_events_since,
        }

    # --- entry points ----------------------------------------------------------

    def handle_native(self, native: Any, codec: ModuleType, protocol: str) -> tuple[int, Any]:
        reply = self.handle_decoded(native, codec, protocol)
        return reply.status_code, codec.encode_reply(reply, native)

    def handle_decoded(self, native: Any, codec: ModuleType, protocol: str) -> Reply:
        try:
            env = codec.decode_request(native)
        except ValueError as exc:  # CodecError and pydantic.ValidationError
            ident = codec.peek(native)
            schema = ident["schema"]
            if isinstance(schema, str) and not schema_supported(schema):
                return self.reject_schema(ident)
            return _validation_reply(exc)
        if not schema_supported(env.schema_):
            return self.reject_schema(
                {
                    "schema": env.schema_,
                    "request_id": env.request_id,
                    "sender": env.sender,
                    "op": env.op,
                }
            )
        return self.handle(env, protocol)

    def reject_schema(self, ident: dict[str, Any]) -> Reply:
        with self.db.transaction() as tx:
            tx.event("schema_rejected", str(ident.get("sender") or "unknown"), data=ident)
        return Reply.failure(
            "schema_unsupported",
            f"schema {ident['schema']!r} is not supported",
            {"supported": [SCHEMA_VERSION]},
        )

    def handle(self, env: Envelope, protocol: str) -> Reply:
        op = self.ops[env.op]
        if env.op in READ_ONLY_OPS:
            with self.db.read() as tx:
                try:
                    return Reply.success(op(tx, env, protocol))
                except BridgeError as exc:
                    return exc.reply()
        with self.db.transaction() as tx:
            digest = inbox.body_hash(env)
            stored = inbox.lookup(tx, env.request_id)
            if stored is not None:
                return self._repeat(tx, env, stored, digest)
            try:
                with tx.savepoint():
                    result = op(tx, env, protocol)
                reply = Reply.success(result, tx.last_event_seq or events.last_seq(tx))
            except (BridgeError, sm.InvalidTransition) as exc:
                reply = self._refuse(tx, env, exc)
            inbox.store(tx, env, digest, reply)
        return reply

    def _repeat(self, tx: Tx, env: Envelope, stored: Any, digest: str) -> Reply:
        ident = {"request_id": env.request_id, "op": env.op}
        if stored["body_hash"] != digest:
            tx.event("request_id_conflict", env.sender, env.dialog_id, env.task_id, ident)
            return Reply.failure(
                "request_id_conflict",
                "request_id was already used with a different body",
                {"request_id": env.request_id},
            )
        tx.event("duplicate_request", env.sender, env.dialog_id, env.task_id, ident)
        return inbox.stored_reply(stored)

    def _refuse(self, tx: Tx, env: Envelope, exc: Exception) -> Reply:
        if isinstance(exc, sm.InvalidTransition):
            reply = Reply.failure(
                "invalid_transition",
                str(exc),
                {"from": exc.from_state, "to": exc.to_state, "reason": exc.reason},
            )
        else:
            reply = exc.reply()
        event_type = REFUSAL_EVENTS.get(reply.error.code)
        if event_type:
            data = {"request_id": env.request_id, "op": env.op, "fence": env.fence}
            data.update(reply.error.details)
            tx.event(event_type, env.sender, env.dialog_id, env.task_id, data)
        return reply

    # --- ops -------------------------------------------------------------------

    def op_register(self, tx: Tx, env: Envelope, protocol: str) -> dict[str, Any]:
        p = RegisterPayload.model_validate(env.payload)
        return registry.register(tx, env.sender, p.protocol, p.base_url)

    def op_heartbeat(self, tx: Tx, env: Envelope, protocol: str) -> dict[str, Any]:
        registry.touch(tx, env.sender, protocol)
        return {"leases": leases.heartbeat(tx, env, self.settings.lease_seconds)}

    def op_dialog_create(self, tx: Tx, env: Envelope, protocol: str) -> dict[str, Any]:
        return tasks.create_dialog(tx, env, self.settings.max_attempts)

    def op_dialog_get(self, tx: Tx, env: Envelope, protocol: str) -> dict[str, Any]:
        view = tasks.dialog_view(tx, env.dialog_id or "")
        if view is None:
            raise BridgeError("not_found", f"unknown dialog {env.dialog_id}")
        return view

    def op_claim(self, tx: Tx, env: Envelope, protocol: str) -> dict[str, Any]:
        task = tasks.load(tx, env)
        leases.claim(tx, task, env, self.settings.lease_seconds)
        return tasks.reload(tx, task["task_id"])

    def op_claim_next(self, tx: Tx, env: Envelope, protocol: str) -> dict[str, Any] | None:
        task = leases.next_claimable(tx)
        if task is None:
            return None
        leases.claim(tx, task, env, self.settings.lease_seconds)
        return tasks.reload(tx, task["task_id"])

    def op_resume(self, tx: Tx, env: Envelope, protocol: str) -> dict[str, Any]:
        ids = leases.resume(tx, env, self.settings.lease_seconds)
        return {"tasks": [tasks.reload(tx, task_id) for task_id in ids]}

    def op_events_since(self, tx: Tx, env: Envelope, protocol: str) -> dict[str, Any]:
        p = EventsSincePayload.model_validate(env.payload)
        return {
            "events": events.since(tx, p.seq, limit=EVENTS_PAGE),
            "last_seq": events.last_seq(tx),
        }
