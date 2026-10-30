"""Allowed task transitions and their guards. See docs/STATE_MACHINE.md.

Every state change in the bridge goes through transition(). Guards are
separate pure functions so each can be unit-tested on its own; they take
plain values (states, flags, bridge-clock floats) rather than database rows.
"""

from __future__ import annotations

from typing import get_args

from bridge.models import State

STATES: tuple[str, ...] = get_args(State)
TERMINAL = frozenset({"COMPLETED", "FAILED", "CANCELLED"})

ALLOWED = frozenset(
    {
        ("CREATED", "AWAITING_CONFIRMATION"),
        ("CREATED", "DISPATCHED"),
        ("AWAITING_CONFIRMATION", "DISPATCHED"),
        ("DISPATCHED", "IN_PROGRESS"),
        ("IN_PROGRESS", "EFFECT_PENDING"),
        ("IN_PROGRESS", "COMPLETED"),
        ("EFFECT_PENDING", "COMPLETED"),
        ("IN_PROGRESS", "SUSPENDED"),
        ("EFFECT_PENDING", "SUSPENDED"),
        ("SUSPENDED", "IN_PROGRESS"),
        ("IN_PROGRESS", "IN_PROGRESS"),  # early resume by the owner
        ("EFFECT_PENDING", "IN_PROGRESS"),  # early resume by the owner
        ("SUSPENDED", "FAILED"),
        ("IN_PROGRESS", "FAILED"),
        ("CREATED", "CANCELLED"),
        ("AWAITING_CONFIRMATION", "CANCELLED"),
        ("DISPATCHED", "CANCELLED"),
        ("IN_PROGRESS", "CANCELLED"),
        ("SUSPENDED", "CANCELLED"),
    }
)

# States in which an owner may write checkpoints, effect results, etc.
WORKING = frozenset({"IN_PROGRESS", "EFFECT_PENDING"})


class InvalidTransition(Exception):
    def __init__(self, from_state: str, to_state: str, reason: str = "") -> None:
        self.from_state = from_state
        self.to_state = to_state
        self.reason = reason
        detail = f" ({reason})" if reason else ""
        super().__init__(f"{from_state} -> {to_state} not allowed{detail}")


def is_allowed(from_state: str, to_state: str) -> bool:
    return (from_state, to_state) in ALLOWED


def transition(from_state: str, to_state: str, guard_ok: bool, reason: str = "") -> str:
    """Return to_state if the move is allowed and its guard holds, else raise."""
    if not is_allowed(from_state, to_state):
        raise InvalidTransition(from_state, to_state, reason or "not in the transition table")
    if not guard_ok:
        raise InvalidTransition(from_state, to_state, reason or "guard failed")
    return to_state


# --- guards ------------------------------------------------------------------


def deps_completed(dep_states: list[str]) -> bool:
    return all(s == "COMPLETED" for s in dep_states)


def deps_broken(dep_states: list[str]) -> bool:
    return any(s in ("FAILED", "CANCELLED") for s in dep_states)


def dispatcher_target(
    state: str, needs_confirmation: bool, confirmed: bool, dep_states: list[str]
) -> str | None:
    """Where the dispatcher moves a task this tick, or None to leave it."""
    if state not in ("CREATED", "AWAITING_CONFIRMATION"):
        return None
    if deps_broken(dep_states):
        return "CANCELLED"
    if not deps_completed(dep_states):
        return None
    if needs_confirmation and not confirmed:
        return "AWAITING_CONFIRMATION" if state == "CREATED" else None
    return "DISPATCHED"


def lease_free(lease_until: float | None, now: float) -> bool:
    """No valid lease is held by anyone (bridge clock)."""
    return lease_until is None or lease_until < now


def lease_expired(lease_until: float | None, now: float) -> bool:
    return lease_until is not None and lease_until < now


def can_record_intent(effect_status: str, current_key: str | None, key: str) -> bool:
    """First intent, or the same intent again after a resume (same key)."""
    if effect_status == "none":
        return True
    return effect_status == "intent" and current_key == key


def can_complete(state: str, action: str | None, effect_status: str) -> bool:
    if state == "IN_PROGRESS":
        return action is None or effect_status == "committed"
    if state == "EFFECT_PENDING":
        return effect_status == "committed"
    return False


def can_resume(attempt: int, max_attempts: int) -> bool:
    return attempt < max_attempts


def retries_exhausted(attempt: int, max_attempts: int) -> bool:
    return attempt >= max_attempts


def can_cancel(state: str, effect_status: str) -> bool:
    """A task whose side effect may already have happened is never cancelled."""
    if state in ("CREATED", "AWAITING_CONFIRMATION", "DISPATCHED"):
        return True
    if state in ("IN_PROGRESS", "SUSPENDED"):
        return effect_status == "none"
    return False


def lease_expiry_targets(attempt: int, max_attempts: int) -> list[str]:
    """Watchdog path for an expired lease: always via SUSPENDED, then FAILED at the limit."""
    if retries_exhausted(attempt, max_attempts):
        return ["SUSPENDED", "FAILED"]
    return ["SUSPENDED"]


def fail_target(retryable: bool) -> str:
    """task.fail: a retryable failure suspends the task so it can be resumed or reclaimed."""
    return "SUSPENDED" if retryable else "FAILED"


def can_checkpoint(state: str) -> bool:
    return state in WORKING


def expected_effect_key(task_id: str, action: str) -> str:
    """The side-effect idempotency key. Never includes the fence or the attempt."""
    return f"{task_id}:{action}"
