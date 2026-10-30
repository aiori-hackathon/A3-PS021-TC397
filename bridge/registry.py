"""Agent registry: known agents, protocol, last heartbeat. See docs/BRIDGE.md."""

from __future__ import annotations

import sqlite3
from typing import Any

from bridge.db import Tx, iso


def record(row: sqlite3.Row) -> dict[str, Any]:
    last = row["last_heartbeat"]
    return {
        "agent_id": row["agent_id"],
        "protocol": row["protocol"],
        "base_url": row["base_url"],
        "last_heartbeat": iso(last) if last is not None else None,
        "status": row["status"],
    }


def register(tx: Tx, agent_id: str, protocol: str, base_url: str) -> dict[str, Any]:
    tx.execute(
        "INSERT INTO agents (agent_id, protocol, base_url, last_heartbeat, status)"
        " VALUES (?,?,?,?, 'online')"
        " ON CONFLICT(agent_id) DO UPDATE SET protocol = excluded.protocol,"
        " base_url = excluded.base_url, last_heartbeat = excluded.last_heartbeat,"
        " status = 'online'",
        (agent_id, protocol, base_url, tx.now),
    )
    tx.event("agent_registered", agent_id, data={"protocol": protocol, "base_url": base_url})
    return get(tx, agent_id)


def touch(tx: Tx, agent_id: str, protocol: str) -> None:
    """A heartbeat: the agent is alive. Unknown agents are added with an empty base_url."""
    tx.execute(
        "INSERT INTO agents (agent_id, protocol, base_url, last_heartbeat, status)"
        " VALUES (?,?, '', ?, 'online')"
        " ON CONFLICT(agent_id) DO UPDATE SET last_heartbeat = excluded.last_heartbeat,"
        " status = 'online'",
        (agent_id, protocol, tx.now),
    )


def get(tx: Tx, agent_id: str) -> dict[str, Any]:
    return record(tx.one("SELECT * FROM agents WHERE agent_id = ?", (agent_id,)))


def all_agents(tx: Tx) -> list[dict[str, Any]]:
    return [record(r) for r in tx.all("SELECT * FROM agents ORDER BY agent_id")]
