"""U: scoring one seeded campaign run (tests/helpers/campaign.py). No servers."""

from tests.helpers.campaign import score


def dialog(msg_id, dialog_id, task_ids, created_at):
    return {
        "msg_id": msg_id,
        "dialog_id": dialog_id,
        "task_ids": task_ids,
        "created_at": created_at,
    }


def task(task_id, action, state="COMPLETED"):
    return {"task_id": task_id, "action": action, "state": state}


def row(n, task_id, action, amount=None):
    params = {"amount": amount, "currency": "INR"} if amount is not None else {}
    return {
        "id": n,
        "task_id": task_id,
        "action": action,
        "idempotency_key": f"{task_id}:{action}",
        "params": params,
    }


def test_clean_run_has_no_extras():
    dialogs = [dialog("c0", "d1", ["t1", "t2"], "1"), dialog("c1", "d2", ["t3"], "2")]
    tasks = [task("t1", "pay"), task("t2", "send"), task("t3", None)]
    ledger = [row(1, "t1", "pay", 40), row(2, "t2", "send")]
    s = score(dialogs, tasks, ledger)
    assert s["messages"] == 2 and s["dialogs"] == 2 and s["extra_dialogs"] == 0
    assert s["duplicate_side_effects"] == 0 and s["duplicate_payments"] == 0
    assert s["money_paid_twice"] == 0 and s["keys_performed_twice"] == 0
    assert s["stuck_tasks"] == 0 and s["completed"] == 3 and s["ledger_rows"] == 2


def test_same_key_twice_is_a_duplicate_with_its_amount():
    dialogs = [dialog("c0", "d1", ["t1"], "1")]
    ledger = [row(1, "t1", "pay", 40), row(2, "t1", "pay", 40)]
    s = score(dialogs, [task("t1", "pay")], ledger)
    assert s["duplicate_side_effects"] == 1 and s["duplicate_payments"] == 1
    assert s["money_paid_twice"] == 40 and s["keys_performed_twice"] == 1
    assert s["payments_without_amount"] == 0


def test_payment_without_an_amount_is_counted_but_adds_no_money():
    dialogs = [dialog("c0", "d1", ["t1"], "1")]
    ledger = [row(1, "t1", "pay"), row(2, "t1", "pay")]
    s = score(dialogs, [task("t1", "pay")], ledger)
    assert s["duplicate_payments"] == 1 and s["money_paid_twice"] == 0
    assert s["payments_without_amount"] == 1


def test_second_dialog_for_one_message_counts_its_effects_as_extra():
    # A lost reply to dialog.create, retried with dedup off: a second dialog with new
    # task ids, so new effect keys. The ledger key check cannot see it; per message it is
    # still one payment too many.
    dialogs = [dialog("c0", "d1", ["t1"], "1"), dialog("c0", "d2", ["t9"], "2")]
    tasks = [task("t1", "pay"), task("t9", "pay")]
    ledger = [row(1, "t1", "pay", 120), row(2, "t9", "pay", 120)]
    s = score(dialogs, tasks, ledger)
    assert s["messages"] == 1 and s["dialogs"] == 2 and s["extra_dialogs"] == 1
    assert s["duplicate_payments"] == 1 and s["money_paid_twice"] == 120
    assert s["keys_performed_twice"] == 0


def test_failed_task_is_not_an_extra_and_retry_in_another_dialog_is_fine():
    # The first dialog's payment failed; one payment in total is what was asked for.
    dialogs = [dialog("c0", "d1", ["t1"], "1"), dialog("c0", "d2", ["t9"], "2")]
    tasks = [task("t1", "pay", "FAILED"), task("t9", "pay")]
    s = score(dialogs, tasks, [row(1, "t9", "pay", 40)])
    assert s["duplicate_side_effects"] == 0 and s["failed"] == 1


def test_stuck_tasks_and_other_messages_are_counted_separately():
    dialogs = [dialog("c0", "d1", ["t1"], "1"), dialog("other", "d5", ["t5"], "3")]
    tasks = [task("t1", "pay", "IN_PROGRESS"), task("t5", "pay")]
    ledger = [row(1, "t5", "pay", 40), row(2, "t5", "pay", 40)]
    s = score(dialogs, tasks, ledger, msg_prefix="c")
    assert s["messages"] == 1 and s["stuck_tasks"] == 1
    assert s["duplicate_side_effects"] == 0  # t5 belongs to a message outside the run


def test_extra_send_is_counted_but_not_as_a_payment():
    dialogs = [dialog("c0", "d1", ["t2"], "1")]
    ledger = [row(1, "t2", "send"), row(2, "t2", "send")]
    s = score(dialogs, [task("t2", "send")], ledger)
    assert s["duplicate_side_effects"] == 1 and s["duplicate_payments"] == 0
    assert s["money_paid_twice"] == 0
