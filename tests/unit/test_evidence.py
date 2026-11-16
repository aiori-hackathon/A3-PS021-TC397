"""U: a worker's three-way answer about a side effect. Unreachable is never 'not done'."""

import pytest

from adapters.evidence import classify


def test_the_ledger_answers_are_passed_through():
    assert classify(200, {"evidence": "VERIFIED_DONE"}) == "VERIFIED_DONE"
    assert classify(200, {"evidence": "VERIFIED_NOT_EXECUTED"}) == "VERIFIED_NOT_EXECUTED"


@pytest.mark.parametrize(
    "status,body",
    [(None, None), (503, {"error": "dropped"}), (500, None), (200, {}), (200, {"evidence": "?"})],
)
def test_anything_else_is_unknown(status, body):
    assert classify(status, body) == "UNKNOWN"
