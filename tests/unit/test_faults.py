"""U: the shared fault-rule engine (docs/FAULT_INJECTION.md). No servers."""

import pytest
from pydantic import ValidationError

from services.faults import FaultEngine, FaultRule

BRIDGE_LINKS = ("agent_a", "agent_b", "agent_b2")


def rule(**kw):
    return FaultRule(**{"link": "agent_a", "action": "drop_reply", **kw})


def test_rule_defaults():
    r = rule()
    assert (r.count, r.delay_ms, r.match, r.note) == (1, 0, {}, "")


@pytest.mark.parametrize(
    "bad", [{"count": 0}, {"count": -2}, {"delay_ms": -1}, {"action": "explode"}, {"link": "mars"}]
)
def test_rule_validation(bad):
    with pytest.raises(ValidationError):
        rule(**bad)


def test_engine_only_accepts_its_links():
    engine = FaultEngine(BRIDGE_LINKS)
    with pytest.raises(ValueError):
        engine.add(rule(link="actions"))


def test_count_one_fires_once_then_disappears():
    engine = FaultEngine(BRIDGE_LINKS)
    added = engine.add(rule(match={"op": "dialog.create"}))
    assert added["id"] == "r1" and added["count"] == 1
    assert engine.take("agent_a", {"op": "task.confirm"}) is None  # no match
    assert engine.take("agent_b", {"op": "dialog.create"}) is None  # other link
    fired = engine.take("agent_a", {"op": "dialog.create"})
    assert fired is not None and fired.id == "r1"
    assert engine.take("agent_a", {"op": "dialog.create"}) is None
    assert engine.list() == []


def test_count_minus_one_lasts_until_deleted():
    engine = FaultEngine(BRIDGE_LINKS)
    added = engine.add(rule(action="cut", count=-1))
    for _ in range(5):
        assert engine.take("agent_a", {"op": "events.since"}).action == "cut"
    assert engine.list()[0]["count"] == -1
    assert engine.delete(added["id"]) is True
    assert engine.delete(added["id"]) is False
    assert engine.take("agent_a", {"op": "events.since"}) is None


def test_remaining_count_is_reported():
    engine = FaultEngine(BRIDGE_LINKS)
    engine.add(rule(count=3))
    engine.take("agent_a", {})
    assert engine.list()[0]["count"] == 2


def test_empty_match_matches_everything_and_first_rule_wins():
    engine = FaultEngine(("actions",))
    engine.add(FaultRule(link="actions", action="delay", delay_ms=10))
    engine.add(FaultRule(link="actions", action="drop_reply", match={"action": "pay"}))
    assert engine.take("actions", {"action": "pay"}).action == "delay"
    assert engine.take("actions", {"action": "pay"}).action == "drop_reply"


def test_clear():
    engine = FaultEngine(BRIDGE_LINKS)
    engine.add(rule())
    engine.add(rule(link="agent_b"))
    assert engine.clear() == 2 and engine.list() == []
    assert engine.add(rule())["id"] == "r3"  # ids are never reused
