"""U: state machine table and guards (docs/STATE_MACHINE.md). No servers."""

import itertools

import pytest

from bridge import state_machine as sm
from bridge.state_machine import ALLOWED, STATES, TERMINAL, InvalidTransition

DOC_TABLE = {
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
    ("IN_PROGRESS", "IN_PROGRESS"),
    ("EFFECT_PENDING", "IN_PROGRESS"),
    ("SUSPENDED", "FAILED"),
    ("IN_PROGRESS", "FAILED"),
    ("CREATED", "CANCELLED"),
    ("AWAITING_CONFIRMATION", "CANCELLED"),
    ("DISPATCHED", "CANCELLED"),
    ("IN_PROGRESS", "CANCELLED"),
    ("SUSPENDED", "CANCELLED"),
}


def test_allowed_matches_the_documented_table_exactly():
    assert ALLOWED == DOC_TABLE


def test_states_and_terminals():
    assert len(STATES) == 9
    assert TERMINAL == {"COMPLETED", "FAILED", "CANCELLED"}


@pytest.mark.parametrize("pair", sorted(DOC_TABLE))
def test_allowed_pair_passes_when_guard_true(pair):
    assert sm.transition(pair[0], pair[1], guard_ok=True) == pair[1]


@pytest.mark.parametrize("pair", sorted(DOC_TABLE))
def test_allowed_pair_rejected_when_guard_false(pair):
    with pytest.raises(InvalidTransition) as exc:
        sm.transition(pair[0], pair[1], guard_ok=False)
    assert (exc.value.from_state, exc.value.to_state) == pair


@pytest.mark.parametrize("pair", sorted(set(itertools.product(STATES, STATES)) - DOC_TABLE))
def test_every_other_pair_is_rejected(pair):
    with pytest.raises(InvalidTransition):
        sm.transition(pair[0], pair[1], guard_ok=True)


@pytest.mark.parametrize("state", sorted(TERMINAL))
def test_terminal_states_have_no_exits(state):
    assert not any(sm.is_allowed(state, to) for to in STATES)


def test_unknown_state_rejected():
    with pytest.raises(InvalidTransition):
        sm.transition("PAUSED", "IN_PROGRESS", guard_ok=True)


# --- guards -----------------------------------------------------------------


def test_deps():
    assert sm.deps_completed([])
    assert sm.deps_completed(["COMPLETED", "COMPLETED"])
    assert not sm.deps_completed(["COMPLETED", "IN_PROGRESS"])
    assert sm.deps_broken(["COMPLETED", "FAILED"])
    assert sm.deps_broken(["CANCELLED"])
    assert not sm.deps_broken(["COMPLETED", "SUSPENDED"])


@pytest.mark.parametrize(
    "state,needs,confirmed,deps,expected",
    [
        ("CREATED", False, False, [], "DISPATCHED"),
        ("CREATED", True, False, [], "AWAITING_CONFIRMATION"),
        ("CREATED", True, True, [], "DISPATCHED"),
        ("CREATED", True, True, ["IN_PROGRESS"], None),
        ("CREATED", False, False, ["COMPLETED"], "DISPATCHED"),
        ("CREATED", True, True, ["FAILED"], "CANCELLED"),
        ("CREATED", False, False, ["CANCELLED"], "CANCELLED"),
        ("AWAITING_CONFIRMATION", True, False, [], None),
        ("AWAITING_CONFIRMATION", True, True, [], "DISPATCHED"),
        ("AWAITING_CONFIRMATION", True, True, ["FAILED"], "CANCELLED"),
        ("DISPATCHED", True, True, [], None),
        ("IN_PROGRESS", False, False, [], None),
    ],
)
def test_dispatcher_target(state, needs, confirmed, deps, expected):
    assert sm.dispatcher_target(state, needs, confirmed, deps) == expected
    if expected is not None:
        sm.transition(state, expected, guard_ok=True)  # always a legal move


def test_lease_free_and_expired_use_given_clock():
    assert sm.lease_free(None, now=100.0)
    assert sm.lease_free(99.0, now=100.0)
    assert not sm.lease_free(101.0, now=100.0)
    assert not sm.lease_expired(None, now=100.0)
    assert sm.lease_expired(99.9, now=100.0)
    assert not sm.lease_expired(100.0, now=100.0)


@pytest.mark.parametrize(
    "status,current_key,key,ok",
    [
        ("none", None, "t:pay", True),
        ("intent", "t:pay", "t:pay", True),  # repeat after resume, same key
        ("intent", "t:pay", "t:send", False),
        ("committed", "t:pay", "t:pay", False),
    ],
)
def test_can_record_intent(status, current_key, key, ok):
    assert sm.can_record_intent(status, current_key, key) is ok


@pytest.mark.parametrize(
    "state,action,status,ok",
    [
        ("IN_PROGRESS", None, "none", True),
        ("IN_PROGRESS", "pay", "none", False),
        ("IN_PROGRESS", "pay", "intent", False),
        ("IN_PROGRESS", "pay", "committed", True),
        ("EFFECT_PENDING", "pay", "intent", False),
        ("EFFECT_PENDING", "pay", "committed", True),
        ("SUSPENDED", None, "none", False),
    ],
)
def test_can_complete(state, action, status, ok):
    assert sm.can_complete(state, action, status) is ok


def test_attempt_guards():
    assert sm.can_resume(attempt=1, max_attempts=3)
    assert sm.can_resume(attempt=2, max_attempts=3)
    assert not sm.can_resume(attempt=3, max_attempts=3)
    assert sm.retries_exhausted(attempt=3, max_attempts=3)
    assert not sm.retries_exhausted(attempt=2, max_attempts=3)


@pytest.mark.parametrize(
    "state,status,ok",
    [
        ("CREATED", "none", True),
        ("AWAITING_CONFIRMATION", "none", True),
        ("DISPATCHED", "none", True),
        ("IN_PROGRESS", "none", True),
        ("SUSPENDED", "none", True),
        ("IN_PROGRESS", "intent", False),
        ("SUSPENDED", "intent", False),
        ("SUSPENDED", "committed", False),
        ("EFFECT_PENDING", "intent", False),
        ("COMPLETED", "committed", False),
        ("FAILED", "none", False),
    ],
)
def test_can_cancel(state, status, ok):
    assert sm.can_cancel(state, status) is ok


def test_lease_expiry_goes_through_suspended_even_at_the_limit():
    assert sm.lease_expiry_targets(attempt=1, max_attempts=3) == ["SUSPENDED"]
    assert sm.lease_expiry_targets(attempt=3, max_attempts=3) == [
        "SUSPENDED",
        "FAILED",
    ]
    for start in ("IN_PROGRESS", "EFFECT_PENDING"):
        state = start
        for target in sm.lease_expiry_targets(attempt=3, max_attempts=3):
            state = sm.transition(state, target, guard_ok=True)
        assert state == "FAILED"


def test_fail_target():
    assert sm.fail_target(retryable=False) == "FAILED"
    assert sm.fail_target(retryable=True) == "SUSPENDED"


def test_checkpoint_states():
    assert sm.can_checkpoint("IN_PROGRESS")
    assert sm.can_checkpoint("EFFECT_PENDING")
    for state in set(STATES) - {"IN_PROGRESS", "EFFECT_PENDING"}:
        assert not sm.can_checkpoint(state)


def test_effect_key_has_no_fence_or_attempt():
    assert sm.expected_effect_key("tsk_8c1f0a2b9e44", "pay") == "tsk_8c1f0a2b9e44:pay"
