"""Request dedup: stores the reply to every state-changing request_id.
See docs/DEDUP_AND_RECOVERY.md#layer-1-the-inbox.

The stored reply is returned unchanged on a repeat, including stored errors.
The body hash leaves out `sent_at`, because a retry legitimately carries a
new send time; everything else must match or the reuse is a conflict.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3

from bridge.db import Tx, dumps
from bridge.models import Envelope, Reply


def body_hash(env: Envelope) -> str:
    body = env.model_dump(mode="json", exclude={"sent_at"})
    return hashlib.sha256(dumps(body).encode()).hexdigest()


def lookup(tx: Tx, request_id: str) -> sqlite3.Row | None:
    return tx.one("SELECT * FROM inbox WHERE request_id = ?", (request_id,))


def stored_reply(row: sqlite3.Row) -> Reply:
    return Reply.from_wire(json.loads(row["reply"]))


def store(tx: Tx, env: Envelope, digest: str, reply: Reply) -> None:
    # OR REPLACE only matters with DISABLE_DEDUP, when repeats are not looked up first.
    tx.execute(
        "INSERT OR REPLACE INTO inbox (request_id, body_hash, op, status_code, reply, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (env.request_id, digest, env.op, reply.status_code, dumps(reply.to_wire()), tx.ts),
    )
