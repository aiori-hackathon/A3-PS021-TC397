"""U: every protection event says which rule fired and why, in plain words. No servers."""

import pytest

from bridge.rules import PROTECTION_EVENTS, explain

CASES = [
    (
        "duplicate_request",
        {"request_id": "msg_1:create", "op": "dialog.create"},
        "INBOX_REPEAT",
        ["msg_1:create", "stored reply", "dialog.create"],
    ),
    (
        "request_id_conflict",
        {"request_id": "msg_1:create", "op": "dialog.create"},
        "REQUEST_ID_REUSED",
        ["msg_1:create", "different body"],
    ),
    (
        "stale_fence_blocked",
        {"op": "task.checkpoint", "fence": 1, "current_fence": 2},
        "FENCE_STALE",
        ["task.checkpoint", "fence 1", "fence 2", "newer owner"],
    ),
    (
        "invalid_transition_rejected",
        {"op": "task.cancel", "from": "EFFECT_PENDING", "to": "CANCELLED"},
        "TRANSITION_NOT_ALLOWED",
        ["task.cancel", "EFFECT_PENDING", "CANCELLED"],
    ),
    (
        "schema_rejected",
        {"schema": "dialog-state/9.9"},
        "SCHEMA_UNSUPPORTED",
        ["dialog-state/9.9", "dialog-state/0.1-experimental"],
    ),
    (
        "duplicate_rejected",
        {"effect_key": "tsk_1:pay", "receipt_id": "rcp_7"},
        "EFFECT_KEY_SEEN",
        ["tsk_1:pay", "rcp_7", "nothing is performed again"],
    ),
]


@pytest.mark.parametrize("event_type,data,rule,words", CASES)
def test_rule_and_reason(event_type, data, rule, words):
    said = explain(event_type, data)
    assert said["rule"] == rule
    for word in words:
        assert word in said["reason"], (word, said["reason"])


def test_every_protection_event_type_is_explained():
    assert set(PROTECTION_EVENTS) == {c[0] for c in CASES}


def test_other_events_are_left_alone():
    assert explain("checkpoint", {"step": "parse"}) is None


def test_missing_fields_never_break_an_explanation():
    assert explain("stale_fence_blocked", {})["rule"] == "FENCE_STALE"
    assert "reason" in explain("invalid_transition_rejected", {"reason": "already finished"})
