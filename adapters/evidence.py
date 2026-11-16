"""A worker's three-way answer about a side effect (docs/DEDUP_AND_RECOVERY.md#evidence).

    VERIFIED_DONE          the actions ledger holds the key: the effect happened
    VERIFIED_NOT_EXECUTED  the ledger answered and does not hold it
    UNKNOWN                no trustworthy answer (unreachable, timeout, error)

UNKNOWN never means "not done". The worker's safe move in every case is the same:
retry with the same key, which the ledger answers with the original receipt if the
effect already happened. The evidence only explains what the worker found out.
"""

from __future__ import annotations

from typing import Any

ANSWERS = ("VERIFIED_DONE", "VERIFIED_NOT_EXECUTED")


def classify(status_code: int | None, body: dict[str, Any] | None) -> str:
    if status_code == 200 and isinstance(body, dict) and body.get("evidence") in ANSWERS:
        return body["evidence"]
    return "UNKNOWN"
