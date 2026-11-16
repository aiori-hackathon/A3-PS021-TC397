"""U: rebuild every task from the event log alone and compare it with the database."""

from bridge.db import Database
from bridge.verify import compare, rebuild, verify


def ev(seq, type, task_id="t1", **data):
    return {"seq": seq, "type": type, "task_id": task_id, "data": data}


HAPPY = [
    ev(1, "task_created"),
    ev(2, "state_changed", **{"from": "CREATED", "to": "DISPATCHED"}),
    ev(3, "claimed", fence=1, attempt=1),
    ev(4, "state_changed", **{"from": "DISPATCHED", "to": "IN_PROGRESS"}),
    ev(5, "effect_intent", effect_key="t1:pay"),
    ev(6, "state_changed", **{"from": "IN_PROGRESS", "to": "EFFECT_PENDING"}),
    ev(7, "effect_committed", effect_key="t1:pay", receipt_id="rcp_1"),
    ev(8, "state_changed", **{"from": "EFFECT_PENDING", "to": "COMPLETED"}),
]


def test_rebuild_follows_the_events():
    t = rebuild(HAPPY)["t1"]
    assert (t["state"], t["fence"], t["attempt"], t["effect"]) == ("COMPLETED", 1, 1, "committed")


def test_a_resume_moves_fence_and_attempt_and_a_hold_is_only_for_suspended():
    events = [
        *HAPPY[:4],
        ev(9, "state_changed", **{"from": "IN_PROGRESS", "to": "SUSPENDED"}),
        ev(10, "held"),
        ev(11, "released"),
        ev(12, "resumed", fence=2, attempt=2),
        ev(13, "state_changed", **{"from": "SUSPENDED", "to": "IN_PROGRESS"}),
    ]
    t = rebuild(events)["t1"]
    assert (t["state"], t["fence"], t["attempt"], t["held"]) == ("IN_PROGRESS", 2, 2, False)
    held = rebuild([*events[:5], ev(10, "held")])["t1"]
    assert held["held"] is True


def row(**kw):
    base = {"task_id": "t1", "state": "COMPLETED", "fence": 1, "attempt": 1}
    base |= {"effect_status": "committed", "held": 0}
    return base | kw


def test_compare_is_silent_when_log_and_database_agree():
    assert compare(rebuild(HAPPY), [row()]) == []


def test_compare_names_each_difference():
    bad = compare(rebuild(HAPPY), [row(state="FAILED", fence=3)])
    assert {(m["field"], m["database"], m["from_log"]) for m in bad} == {
        ("state", "FAILED", "COMPLETED"),
        ("fence", 3, 1),
    }
    assert compare(rebuild(HAPPY), []) == [
        {"task_id": "t1", "field": "task", "database": None, "from_log": "present"}
    ]


def test_a_gap_in_the_sequence_is_reported(tmp_path):
    db = Database(tmp_path / "b.db")
    with db.transaction() as tx:
        tx.event("task_created", "agent_a", "d1", "t1", {"index": 1})
        tx.event("checkpoint", "agent_b", "d1", "t1", {})
    with db.read() as tx:
        assert verify(tx)["seq_contiguous"] is True
    db.conn.execute("PRAGMA writable_schema = ON")  # bypass: drop the guard on purpose
    db.conn.executescript("DROP TRIGGER events_no_delete;")
    db.conn.execute("DELETE FROM events WHERE seq = 1")
    with db.read() as tx:
        assert verify(tx)["seq_contiguous"] is False


def test_the_hash_is_the_same_every_time_and_changes_with_the_state(tmp_path):
    first = rebuild(HAPPY)
    again = rebuild(list(HAPPY))
    from bridge.verify import state_hash

    assert state_hash(first) == state_hash(again)
    other = rebuild(HAPPY[:7])
    assert state_hash(other) != state_hash(first)
