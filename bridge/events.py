"""Numbered event log and WebSocket fan-out after commit. See docs/BRIDGE.md.

Events are written with Tx.event() inside the request's transaction. After
COMMIT the Database calls Hub.notify(), which only wakes each WebSocket
client; the client's sender then reads the new events back from the table.
So a client sees committed events only, in seq order, with no gaps.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import Any

from bridge.db import Tx

GROUPS: dict[str, frozenset[str]] = {
    "lifecycle": frozenset(
        {
            "dialog_created",
            "task_created",
            "state_changed",
            "confirmed",
            "cancelled",
            "claimed",
            "resumed",
            "checkpoint",
            "effect_intent",
            "effect_committed",
            "completed",
            "failed",
        }
    ),
    "protection": frozenset(
        {
            "duplicate_request",
            "duplicate_rejected",
            "stale_fence_blocked",
            "invalid_transition_rejected",
            "schema_rejected",
            "request_id_conflict",
        }
    ),
    "health": frozenset(
        {
            "agent_registered",
            "agent_unresponsive",
            "lease_expired",
            "reclaimed",
            "max_attempts_reached",
            "bridge_started",
        }
    ),
    "testing": frozenset({"fault_rule_set", "fault_applied", "test_step"}),
}


def row_to_event(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "seq": row["seq"],
        "ts": row["ts"],
        "dialog_id": row["dialog_id"],
        "task_id": row["task_id"],
        "type": row["type"],
        "actor": row["actor"],
        "data": json.loads(row["data"]),
    }


def since(
    tx: Tx,
    seq: int = 0,
    task_id: str | None = None,
    type: str | None = None,
    dialog_id: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM events WHERE seq > ?"
    params: list[Any] = [seq]
    for column, value in (("task_id", task_id), ("type", type), ("dialog_id", dialog_id)):
        if value is not None:
            sql += f" AND {column} = ?"
            params.append(value)
    sql += " ORDER BY seq"
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return [row_to_event(r) for r in tx.all(sql, params)]


def last_seq(tx: Tx) -> int:
    return tx.one("SELECT COALESCE(MAX(seq), 0) FROM events")[0]


class Hub:
    """Wakes WebSocket senders after a commit. Safe to call from any thread."""

    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.clients: set[asyncio.Event] = set()

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def notify(self, events: list[dict[str, Any]]) -> None:
        if not events or self.loop is None or self.loop.is_closed():
            return
        for wake in list(self.clients):
            self.loop.call_soon_threadsafe(wake.set)
