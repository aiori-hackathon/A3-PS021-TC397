"""U: rule-based intent router table (docs/INTENT_ROUTING.md). No servers."""

import pytest

from adapters.router import route
from adapters.templates import TEMPLATES


def intents(result):
    return [t["intent"] for t in result.tasks]


# The starting table from INTENT_ROUTING.md.


def test_pay_bakery():
    r = route("pay ₹40 to the bakery")
    assert intents(r) == ["payment"]
    assert r.tasks[0]["params"]["amount"] == 40
    assert r.tasks[0]["params"]["payee"] == "bakery"
    assert r.tasks[0]["params"]["currency"] == "INR"


def test_check_payment_history_is_lookup():
    assert intents(route("check my payment history")) == ["lookup"]


def test_dont_pay_yet_makes_no_tasks():
    r = route("don't pay yet")
    assert r.tasks == []
    assert r.command is None
    assert r.reply  # Agent A tells the user nothing was scheduled


def test_pay_then_email_depends_on_first():
    r = route("pay the bill and email the receipt to Rahul")
    assert intents(r) == ["payment", "message"]
    assert r.tasks[0]["depends_on"] == []
    assert r.tasks[1]["depends_on"] == [1]
    assert r.tasks[1]["params"]["to"] == "Rahul"


def test_booking_day_and_time():
    r = route("book a room for friday 5pm")
    assert intents(r) == ["booking"]
    assert r.tasks[0]["params"]["day"] == "friday"
    assert r.tasks[0]["params"]["time"] == "5pm"


def test_send_money_is_payment():
    r = route("send money to Asha")
    assert intents(r) == ["payment"]
    assert r.tasks[0]["params"]["payee"] == "Asha"


def test_bread_and_butter_is_one_general_clause():
    assert intents(route("bread and butter")) == ["general"]


def test_poem_is_general():
    assert intents(route("write me a poem")) == ["general"]


def test_cancel_the_payment_is_a_command():
    r = route("cancel the payment")
    assert r.tasks == []
    assert r.command == {"type": "cancel", "intent": "payment"}


def test_find_booking_ref_is_lookup():
    r = route("find booking HK-204")
    assert intents(r) == ["lookup"]
    assert r.tasks[0]["params"]["ref"] == "HK-204"


# Extra cases.


def test_doc_output_shape_exactly():
    r = route("Pay ₹40 to the bakery and email the receipt to Rahul")
    assert r.tasks == [
        {
            "intent": "payment",
            "action": "pay",
            "params": {"amount": 40, "currency": "INR", "payee": "bakery"},
            "steps": ["parse", "plan", "pay", "verify", "report"],
            "needs_confirmation": True,
            "depends_on": [],
        },
        {
            "intent": "message",
            "action": "send",
            "params": {"to": "Rahul", "body": "receipt"},
            "steps": ["parse", "draft", "send", "report"],
            "needs_confirmation": True,
            "depends_on": [1],
        },
    ]


@pytest.mark.parametrize(
    "text,amount",
    [
        ("pay rs 40 to the bakery", 40),
        ("pay Rs. 40 to the bakery", 40),
        ("pay INR 40.50 to the bakery", 40.5),
        ("pay 40 rupees to the bakery", 40),
        ("pay ₹ 1200 to the landlord", 1200),
    ],
)
def test_amount_forms(text, amount):
    assert route(text).tasks[0]["params"]["amount"] == amount


def test_missing_amount_is_absent_and_payee_defaults():
    p = route("pay the bill").tasks[0]["params"]
    assert "amount" not in p
    assert p["payee"] == "unknown"
    assert p["currency"] == "INR"


def test_curly_apostrophe_negation():
    assert route("don’t pay yet").tasks == []


def test_cancel_by_task_id():
    r = route("cancel tsk_8c1f0a2b9e44")
    assert r.command == {"type": "cancel", "task_id": "tsk_8c1f0a2b9e44"}


def test_cancel_without_reference_does_nothing():
    r = route("cancel")
    assert r.tasks == [] and r.command is None


def test_three_clauses_chain_dependencies():
    r = route("pay ₹40 to the bakery then email the receipt to Rahul and book a table for friday")
    assert intents(r) == ["payment", "message", "booking"]
    assert [t["depends_on"] for t in r.tasks] == [[], [1], [2]]


def test_buy_extracts_item():
    r = route("buy two tickets")
    assert intents(r) == ["payment"]
    assert r.tasks[0]["params"]["item"] == "two tickets"


def test_notify_the_team_is_message():
    r = route("notify the team")
    assert intents(r) == ["message"]
    assert r.tasks[0]["params"]["to"] == "team"


def test_reserve_and_time_with_minutes():
    r = route("reserve a table for tomorrow 7:30 pm")
    assert intents(r) == ["booking"]
    assert r.tasks[0]["params"]["day"] == "tomorrow"
    assert r.tasks[0]["params"]["time"] == "7:30 pm"


def test_matched_explains_each_decision():
    r = route("pay ₹40 to the bakery and check my payment history")
    assert [m["intent"] for m in r.matched] == ["payment", "lookup"]
    # Keywords are tried in the documented order, so "history" fires before "check".
    assert [m["keyword"] for m in r.matched] == ["pay", "history"]
    assert route("write me a poem").matched[0]["keyword"] is None


def test_clause_without_keyword_is_not_split_off():
    # "write me a poem" has no keyword, so the split rule keeps one clause.
    assert intents(route("pay ₹40 to the bakery and write me a poem")) == ["payment"]


def test_needs_confirmation_iff_side_effect():
    for name, tpl in TEMPLATES.items():
        r_needs = tpl["action"] is not None
        assert tpl["needs_confirmation"] is r_needs, name
        if tpl["action"]:
            assert tpl["action"] in tpl["steps"], name


def test_route_is_deterministic():
    text = "Pay ₹40 to the bakery and email the receipt to Rahul"
    assert route(text) == route(text)
