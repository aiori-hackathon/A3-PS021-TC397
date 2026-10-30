"""Mock actions service: side effects with an idempotent ledger and fence checks.
See docs/ACTIONS_SERVICE.md.

It stands in for a payment gateway, mail server and booking system. Each
"side effect" is one ledger row. A repeated idempotency key returns the
original receipt with replayed=true and writes nothing; a fence lower than
the highest seen for the task is rejected. This guard is independent of the
bridge, so even a bridge bug cannot cause a second payment for one key.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import config
from bridge.db import iso

log = logging.getLogger("actions")
logging.basicConfig(level=logging.INFO, format="%(asctime)s actions %(message)s")

SETTINGS = config.load()

LEDGER_SQL = """
CREATE TABLE IF NOT EXISTS ledger (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  idempotency_key TEXT NOT NULL,
  receipt_id      TEXT NOT NULL UNIQUE,
  task_id         TEXT NOT NULL,
  action          TEXT NOT NULL,
  params          TEXT NOT NULL,
  fence           INTEGER NOT NULL,
  performed_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fences (
  task_id   TEXT PRIMARY KEY,
  max_fence INTEGER NOT NULL
);
"""
KEY_INDEX_SQL = "CREATE UNIQUE INDEX IF NOT EXISTS ledger_key ON ledger(idempotency_key)"


class ActionRequest(BaseModel):
    idempotency_key: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    fence: int = Field(ge=1)
    action: Literal["pay", "send", "reserve"]
    params: dict[str, Any] = Field(default_factory=dict)


def receipt(row: sqlite3.Row, replayed: bool) -> dict[str, Any]:
    params = json.loads(row["params"])
    out: dict[str, Any] = {
        "receipt_id": row["receipt_id"],
        "replayed": replayed,
        "action": row["action"],
        "performed_at": row["performed_at"],
        "idempotency_key": row["idempotency_key"],
        "task_id": row["task_id"],
        "fence": row["fence"],
    }
    if row["action"] == "pay":
        out.update(amount=params.get("amount"), payee=params.get("payee"))
    elif row["action"] == "send":
        out.update(to=params.get("to"))
    elif row["action"] == "reserve":
        out.update(confirmation_code="CNF-" + row["receipt_id"][-6:].upper())
    return out


def ledger_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "idempotency_key": row["idempotency_key"],
        "receipt_id": row["receipt_id"],
        "task_id": row["task_id"],
        "action": row["action"],
        "params": json.loads(row["params"]),
        "fence": row["fence"],
        "performed_at": row["performed_at"],
    }


class Ledger:
    def __init__(self, path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = FULL")
        self.conn.executescript(LEDGER_SQL)
        self.conn.execute(KEY_INDEX_SQL)

    def perform(self, req: ActionRequest) -> tuple[int, dict[str, Any]]:
        with self.lock:
            c = self.conn
            c.execute("BEGIN IMMEDIATE")
            try:
                status, body = self._perform(c, req)
            except BaseException:
                c.execute("ROLLBACK")
                raise
            c.execute("COMMIT")
        return status, body

    def _perform(self, c: sqlite3.Connection, req: ActionRequest) -> tuple[int, dict[str, Any]]:
        found = c.execute(
            "SELECT max_fence FROM fences WHERE task_id = ?", (req.task_id,)
        ).fetchone()
        if found and req.fence < found["max_fence"]:
            log.info(
                "stale fence %s < %s for %s", req.fence, found["max_fence"], req.idempotency_key
            )
            return 409, {
                "error": {
                    "code": "stale_fence",
                    "message": f"fence {req.fence} < current {found['max_fence']}",
                    "details": {"current_fence": found["max_fence"]},
                }
            }
        c.execute(
            "INSERT INTO fences (task_id, max_fence) VALUES (?, ?)"
            " ON CONFLICT(task_id) DO UPDATE SET max_fence = MAX(max_fence, excluded.max_fence)",
            (req.task_id, req.fence),
        )
        row = c.execute(
            "SELECT * FROM ledger WHERE idempotency_key = ? ORDER BY id LIMIT 1",
            (req.idempotency_key,),
        ).fetchone()
        if row is not None:
            log.info("replayed %s -> %s", req.idempotency_key, row["receipt_id"])
            return 200, receipt(row, replayed=True)
        receipt_id = f"rcp_{uuid.uuid4().hex[:12]}"
        c.execute(
            "INSERT INTO ledger (idempotency_key, receipt_id, task_id, action, params, fence,"
            " performed_at) VALUES (?,?,?,?,?,?,?)",
            (
                req.idempotency_key,
                receipt_id,
                req.task_id,
                req.action,
                json.dumps(req.params, sort_keys=True),
                req.fence,
                iso(time.time()),
            ),
        )
        row = c.execute("SELECT * FROM ledger WHERE receipt_id = ?", (receipt_id,)).fetchone()
        log.info("performed %s %s -> %s", req.action, req.idempotency_key, receipt_id)
        return 200, receipt(row, replayed=False)

    def rows(self, task_id: str | None, action: str | None) -> list[sqlite3.Row]:
        sql, params = "SELECT * FROM ledger WHERE 1 = 1", []
        for column, value in (("task_id", task_id), ("action", action)):
            if value is not None:
                sql += f" AND {column} = ?"
                params.append(value)
        with self.lock:
            return self.conn.execute(sql + " ORDER BY id", params).fetchall()

    def by_receipt(self, receipt_id: str) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute(
                "SELECT * FROM ledger WHERE receipt_id = ?", (receipt_id,)
            ).fetchone()


class State:
    ledger: Ledger


state = State()


@asynccontextmanager
async def lifespan(app: FastAPI):
    state.ledger = Ledger(SETTINGS.data_dir / "ledger.db")
    log.info("ledger at %s", SETTINGS.data_dir / "ledger.db")
    yield


app = FastAPI(title="Mock actions service", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(SETTINGS.cors_origins),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/actions")
def perform(req: ActionRequest) -> JSONResponse:
    status, body = state.ledger.perform(req)
    return JSONResponse(body, status_code=status)


@app.get("/ledger")
def ledger(task_id: str | None = None, action: str | None = None) -> list[dict[str, Any]]:
    return [ledger_row(r) for r in state.ledger.rows(task_id, action)]


@app.get("/ledger/count")
def ledger_count(task_id: str | None = None, action: str | None = None) -> dict[str, int]:
    return {"count": len(state.ledger.rows(task_id, action))}


@app.get("/receipts/{receipt_id}")
def get_receipt(receipt_id: str) -> dict[str, Any]:
    row = state.ledger.by_receipt(receipt_id)
    if row is None:
        raise HTTPException(404, f"unknown receipt {receipt_id}")
    return receipt(row, replayed=False)


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True}
