"""Which protection rule fired, and why, in plain words.

Pure: one table from a protection event type to a rule code and a sentence built
from the event's own data. `Tx.event` adds both to the event, so the Inspect log
and the Watch view can say *why* a repeat or a late write was refused, without
anyone reading JSON. See docs/BRIDGE.md#protection-events.
"""

from __future__ import annotations

from typing import Any

from bridge.models import SCHEMA_VERSION


def _g(data: dict[str, Any], key: str, default: str = "?") -> Any:
    value = data.get(key)
    return default if value is None else value


def _inbox_repeat(d: dict[str, Any]) -> str:
    return (
        f"{_g(d, 'request_id')} was already processed, so its stored reply was returned"
        f" instead of running {_g(d, 'op')} again."
    )


def _request_id_reused(d: dict[str, Any]) -> str:
    return (
        f"{_g(d, 'request_id')} was already used with a different body; refused, because one"
        " request ID must never mean two different requests."
    )


def _fence_stale(d: dict[str, Any]) -> str:
    return (
        f"{_g(d, 'op')} carried fence {_g(d, 'fence')}, but the task is already at fence"
        f" {_g(d, 'current_fence')}: a newer owner exists, so this late write was refused."
    )


def _transition_not_allowed(d: dict[str, Any]) -> str:
    why = f" ({d['reason']})" if d.get("reason") else ""
    return (
        f"{_g(d, 'op')}: a task cannot go from {_g(d, 'from')} to {_g(d, 'to')}{why};"
        " the state machine refused it and nothing changed."
    )


def _schema_unsupported(d: dict[str, Any]) -> str:
    return f"schema {_g(d, 'schema')!r} is not supported; only {SCHEMA_VERSION} is."


def _effect_key_seen(d: dict[str, Any]) -> str:
    return (
        f"effect key {_g(d, 'effect_key')} already has receipt {_g(d, 'receipt_id')}; the"
        " original is returned and nothing is performed again."
    )


PROTECTION_EVENTS = {
    "duplicate_request": ("INBOX_REPEAT", _inbox_repeat),
    "request_id_conflict": ("REQUEST_ID_REUSED", _request_id_reused),
    "stale_fence_blocked": ("FENCE_STALE", _fence_stale),
    "invalid_transition_rejected": ("TRANSITION_NOT_ALLOWED", _transition_not_allowed),
    "schema_rejected": ("SCHEMA_UNSUPPORTED", _schema_unsupported),
    "duplicate_rejected": ("EFFECT_KEY_SEEN", _effect_key_seen),
}


def explain(event_type: str, data: dict[str, Any]) -> dict[str, str] | None:
    """{"rule", "reason"} for a protection event, None for any other event."""
    entry = PROTECTION_EVENTS.get(event_type)
    if entry is None:
        return None
    rule, say = entry
    return {"rule": rule, "reason": say(data)}
