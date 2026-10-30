"""SQLite setup (WAL, synchronous=FULL), tables and the transaction() helper.
See docs/BRIDGE.md#database.

One connection, guarded by one threading.Lock. Every state-changing request
runs inside `with db.transaction() as tx:` (BEGIN IMMEDIATE ... COMMIT), which
writes the state change, the inbox reply and the events together. Events
appended in a transaction are handed to `on_commit` only after COMMIT.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS dialogs (
  dialog_id   TEXT PRIMARY KEY,
  msg_id      TEXT NOT NULL UNIQUE,
  text        TEXT NOT NULL,
  created_by  TEXT NOT NULL,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
  task_id            TEXT PRIMARY KEY,
  dialog_id          TEXT NOT NULL REFERENCES dialogs(dialog_id),
  idx                INTEGER NOT NULL,
  intent             TEXT NOT NULL,
  action             TEXT,
  params             TEXT NOT NULL,
  steps              TEXT NOT NULL,
  state              TEXT NOT NULL,
  owner              TEXT,
  lease_until        REAL,
  fence              INTEGER NOT NULL DEFAULT 0,
  attempt            INTEGER NOT NULL DEFAULT 0,
  max_attempts       INTEGER NOT NULL DEFAULT 3,
  depends_on         TEXT NOT NULL DEFAULT '[]',
  needs_confirmation INTEGER NOT NULL DEFAULT 0,
  confirmed          INTEGER NOT NULL DEFAULT 0,
  effect_key         TEXT,
  effect_status      TEXT NOT NULL DEFAULT 'none',
  receipt_id         TEXT,
  effect_replayed    INTEGER NOT NULL DEFAULT 0,
  result             TEXT,
  reason             TEXT,
  created_at         TEXT NOT NULL,
  updated_at         TEXT NOT NULL,
  UNIQUE (dialog_id, idx)
);

CREATE TABLE IF NOT EXISTS checkpoints (
  task_id     TEXT NOT NULL REFERENCES tasks(task_id),
  step_index  INTEGER NOT NULL,
  step        TEXT NOT NULL,
  output      TEXT NOT NULL,
  fence       INTEGER NOT NULL,
  created_at  TEXT NOT NULL,
  PRIMARY KEY (task_id, step_index)
);

CREATE TABLE IF NOT EXISTS inbox (
  request_id  TEXT PRIMARY KEY,
  body_hash   TEXT NOT NULL,
  op          TEXT NOT NULL,
  status_code INTEGER NOT NULL,
  reply       TEXT NOT NULL,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
  seq        INTEGER PRIMARY KEY AUTOINCREMENT,
  ts         TEXT NOT NULL,
  dialog_id  TEXT,
  task_id    TEXT,
  type       TEXT NOT NULL,
  actor      TEXT NOT NULL,
  data       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS events_task ON events(task_id);

CREATE TABLE IF NOT EXISTS agents (
  agent_id       TEXT PRIMARY KEY,
  protocol       TEXT NOT NULL,
  base_url       TEXT NOT NULL,
  last_heartbeat REAL,
  status         TEXT NOT NULL
);
"""


def iso(ts: float) -> str:
    """Bridge-clock epoch seconds -> ISO-8601 UTC with milliseconds."""
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class Tx:
    """One open transaction. `now` is the bridge clock, fixed for the whole transaction."""

    def __init__(self, conn: sqlite3.Connection, now: float) -> None:
        self.conn = conn
        self.now = now
        self.ts = iso(now)
        self.events: list[dict[str, Any]] = []

    def execute(self, sql: str, params: Any = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def one(self, sql: str, params: Any = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    def all(self, sql: str, params: Any = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def event(
        self,
        type: str,
        actor: str,
        dialog_id: str | None = None,
        task_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> int:
        data = data or {}
        cur = self.execute(
            "INSERT INTO events (ts, dialog_id, task_id, type, actor, data) VALUES (?,?,?,?,?,?)",
            (self.ts, dialog_id, task_id, type, actor, dumps(data)),
        )
        seq = cur.lastrowid
        self.events.append(
            {
                "seq": seq,
                "ts": self.ts,
                "dialog_id": dialog_id,
                "task_id": task_id,
                "type": type,
                "actor": actor,
                "data": data,
            }
        )
        return seq

    @property
    def last_event_seq(self) -> int | None:
        return self.events[-1]["seq"] if self.events else None

    @contextmanager
    def savepoint(self, name: str = "op") -> Iterator[None]:
        """Undo the writes (and pending events) of the block if it raises."""
        mark = len(self.events)
        self.execute(f"SAVEPOINT {name}")
        try:
            yield
        except BaseException:
            self.execute(f"ROLLBACK TO {name}")
            self.execute(f"RELEASE {name}")
            del self.events[mark:]
            raise
        self.execute(f"RELEASE {name}")


class Database:
    def __init__(
        self,
        path: Path,
        on_commit: Callable[[list[dict[str, Any]]], None] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.on_commit = on_commit
        self.clock = clock
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = FULL")  # a commit survives a hard kill
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self.conn.executescript(SCHEMA_SQL)

    def integrity_ok(self) -> bool:
        with self.lock:
            return self.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    @contextmanager
    def transaction(self) -> Iterator[Tx]:
        with self.lock:
            tx = Tx(self.conn, self.clock())
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield tx
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            self.conn.execute("COMMIT")
        # Only after commit: listeners never see something that was rolled back.
        if tx.events and self.on_commit:
            self.on_commit(tx.events)

    @contextmanager
    def read(self) -> Iterator[Tx]:
        with self.lock:
            yield Tx(self.conn, self.clock())

    def close(self) -> None:
        with self.lock:
            self.conn.close()
