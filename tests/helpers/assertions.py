"""Shared assertions. See docs/TESTING.md#helpers."""


def assert_events_in_order(events, expected_types):
    """expected_types must appear in this order; other events may sit between."""
    it = iter(e["type"] for e in events)
    missing = [t for t in expected_types if not any(x == t for x in it)]
    assert not missing, f"missing or out of order: {missing}"
