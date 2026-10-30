"""Mock actions service: idempotent ledger and fences (docs/ACTIONS_SERVICE.md).

Runs against a bare actions process with its own ledger file.
"""

from tests.conftest import start_service, stop_process
from tests.helpers.clients import ActionsClient

PAY = {"amount": 40, "currency": "INR", "payee": "bakery"}


def test_same_key_returns_the_same_receipt(actions_service):
    a = actions_service
    first = a.perform("tsk_a:pay", "tsk_a", 1, "pay", PAY)
    assert first.status_code == 200
    r1 = first.json()
    assert r1["replayed"] is False and r1["receipt_id"].startswith("rcp_")
    assert (r1["action"], r1["amount"], r1["payee"]) == ("pay", 40, "bakery")

    second = a.perform("tsk_a:pay", "tsk_a", 1, "pay", PAY).json()
    assert second["receipt_id"] == r1["receipt_id"]
    assert second["replayed"] is True
    assert a.count(task_id="tsk_a", action="pay") == 1
    assert a.receipt(r1["receipt_id"]).json()["receipt_id"] == r1["receipt_id"]
    assert a.receipt("rcp_missing").status_code == 404


def test_new_owner_with_higher_fence_gets_the_original_receipt(actions_service):
    a = actions_service
    r1 = a.perform("tsk_b:pay", "tsk_b", 1, "pay", PAY).json()
    r2 = a.perform("tsk_b:pay", "tsk_b", 2, "pay", PAY).json()
    assert r2["receipt_id"] == r1["receipt_id"] and r2["replayed"] is True
    assert a.count(task_id="tsk_b") == 1


def test_stale_fence_is_rejected(actions_service):
    a = actions_service
    assert a.perform("tsk_c:pay", "tsk_c", 2, "pay", PAY).status_code == 200
    old = a.perform("tsk_c:pay", "tsk_c", 1, "pay", PAY)
    assert old.status_code == 409
    assert old.json()["error"]["code"] == "stale_fence"
    assert old.json()["error"]["details"]["current_fence"] == 2
    assert a.count(task_id="tsk_c") == 1


def test_receipt_fields_per_action_and_filters(actions_service):
    a = actions_service
    sent = a.perform("tsk_d:send", "tsk_d", 1, "send", {"to": "Rahul", "body": "receipt"}).json()
    assert sent["to"] == "Rahul"
    booked = a.perform(
        "tsk_e:reserve", "tsk_e", 1, "reserve", {"what": "room", "day": "friday", "time": "5pm"}
    ).json()
    assert booked["confirmation_code"].startswith("CNF-")
    assert a.count(action="send") == 1 and a.count(action="reserve") == 1
    assert [row["task_id"] for row in a.ledger(action="send")] == ["tsk_d"]
    assert a.perform("k", "t", 1, "teleport").status_code == 422


def test_ledger_survives_a_restart(tmp_path):
    proc, log, url = start_service("services.actions:app", tmp_path, tmp_path / "a.log")
    a = ActionsClient(url)
    r1 = a.perform("tsk_f:pay", "tsk_f", 1, "pay", PAY).json()
    a.close()
    proc.kill()  # hard kill: synchronous=FULL means the row is on disk
    proc.wait()
    log.close()

    proc, log, url = start_service("services.actions:app", tmp_path, tmp_path / "a.log")
    a = ActionsClient(url)
    try:
        again = a.perform("tsk_f:pay", "tsk_f", 1, "pay", PAY).json()
        assert again["receipt_id"] == r1["receipt_id"] and again["replayed"] is True
        assert a.count(task_id="tsk_f") == 1
    finally:
        a.close()
        stop_process(proc, log)
