"""U: the event log is append-only, enforced by SQLite itself. No servers."""

import sqlite3

import pytest

from bridge.db import Database


def database(tmp_path):
    return Database(tmp_path / "bridge.db")


def log_one_event(db):
    with db.transaction() as tx:
        tx.event("dialog_created", "agent_a", "dlg_1", None, {"msg_id": "m1"})


def test_an_event_cannot_be_edited(tmp_path):
    db = database(tmp_path)
    log_one_event(db)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        db.conn.execute("UPDATE events SET type = 'completed' WHERE seq = 1")
    assert (
        db.conn.execute("SELECT type FROM events WHERE seq = 1").fetchone()[0] == "dialog_created"
    )


def test_an_event_cannot_be_deleted(tmp_path):
    db = database(tmp_path)
    log_one_event(db)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        db.conn.execute("DELETE FROM events")
    assert db.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_appending_and_rolling_back_a_failed_request_still_work(tmp_path):
    db = database(tmp_path)
    log_one_event(db)
    with pytest.raises(RuntimeError), db.transaction() as tx:
        tx.event("failed", "watchdog", "dlg_1", None, {})
        raise RuntimeError("the request fails after logging")
    assert db.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    log_one_event(db)
    assert [r[0] for r in db.conn.execute("SELECT seq FROM events ORDER BY seq")] == [
        1,
        2,
    ]  # a rolled-back event leaves no gap


def test_a_database_made_before_the_triggers_gets_them_on_start(tmp_path):
    path = tmp_path / "old.db"
    database_first = Database(path)
    log_one_event(database_first)
    database_first.conn.executescript(
        "DROP TRIGGER events_no_update; DROP TRIGGER events_no_delete;"
    )
    database_first.conn.close()
    reopened = Database(path)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        reopened.conn.execute("DELETE FROM events")
