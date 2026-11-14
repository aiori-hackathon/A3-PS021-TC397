"""Rule-based intent router: text to a list of tasks. See docs/INTENT_ROUTING.md.

Pure and deterministic: keyword rules pick an intent (specific before
general), simple patterns pull out parameters, and each intent maps to a
fixed template. Task n depends on task n-1 within one message.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from adapters.templates import build_task

# Checked in this order; lookup first so "check my payment history" is a lookup,
# payment before message so "send money" is a payment.
INTENT_KEYWORDS: list[tuple[str, list[str]]] = [
    (
        "lookup",
        [
            "history",
            "status",
            "find",
            "search",
            "look up",
            "check",
            "summarise",
            "summarize",
            "show",
            "track",
            "balance",
            "where is",
            "how much",
        ],
    ),
    (
        "payment",
        [
            "pay",
            "buy",
            "purchase",
            "transfer",
            "send money",
            "order",
            "tip",
            "donate",
            "wire",
            "remit",
            "recharge",
            "settle",
        ],
    ),
    (
        "message",
        [
            "email",
            "mail",
            "message",
            "notify",
            "text",
            "tell",
            "send",
            "whatsapp",
            "sms",
            "ping",
            "inform",
            "remind",
        ],
    ),
    ("booking", ["book", "reserve", "schedule", "appointment", "rent", "hire", "meeting"]),
]


def _kw_pattern(kw: str) -> re.Pattern[str]:
    # Word start only, so "pay" also matches "payment" but not "repay".
    return re.compile(r"\b" + r"\s+".join(map(re.escape, kw.split())), re.IGNORECASE)


_KEYWORD_RULES = [(intent, kw, _kw_pattern(kw)) for intent, kws in INTENT_KEYWORDS for kw in kws]

_SPLIT = re.compile(r"(\s+(?:and|then|also)\s+|\s*;\s*|\.(?:\s+|$))", re.IGNORECASE)
_NEGATION = re.compile(r"\b(?:don't|do not|never|cancel|stop|hold off|not yet)\b", re.IGNORECASE)
_CANCEL = re.compile(r"\b(?:cancel|stop)\b", re.IGNORECASE)
_TASK_REF = re.compile(r"\btsk_[0-9a-f]+\b")

_AMOUNT = [
    re.compile(r"(?:₹|\brs\.?|\binr)\s*(\d+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"(\d+(?:\.\d+)?)\s*(?:rupees|rs|inr)\b", re.IGNORECASE),
]
_TO = re.compile(
    r"\bto\s+(?:the\s+)?([\w\- ]+?)(?=$|\s+and\b|\s+then\b|\s+for\b|\s+on\b|,)",
    re.IGNORECASE,
)
_ITEM = re.compile(
    r"\b(?:buy|order|purchase)\s+(.+?)(?=$|\s+for\b|\s+to\b|\s+and\b|,)", re.IGNORECASE
)
_BODY = re.compile(
    r"\b(?:email|mail|message|notify|text|tell|send)\s+(?:the\s+|a\s+|an\s+)?(.+?)\s+to\b",
    re.IGNORECASE,
)
_WHAT = re.compile(
    r"\b(?:book|reserve|schedule|rent|hire)\s+(?:a\s+|an\s+|the\s+)?([\w\-]+)", re.IGNORECASE
)
_DAY = re.compile(
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|today|tomorrow)\b",
    re.IGNORECASE,
)
_TIME = re.compile(r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm))\b", re.IGNORECASE)
_REF = re.compile(r"\b([A-Za-z]{2}-\d{2,4})\b")
# A bare number in a payment clause ("pay rahul 500"), once times and refs are removed.
_BARE_AMOUNT = re.compile(r"(?<![\w.:])(\d+(?:\.\d+)?)(?![\w:])")
# "pay rahul 500", "tip the driver 50": the payee right after the verb, when there is no "to".
_PAYEE_AFTER_VERB = re.compile(
    r"\b(?:pay|tip|donate|wire|remit|settle)\s+(?:the\s+|my\s+)?(?![₹\d])([A-Za-z][\w\-]*)",
    re.IGNORECASE,
)
# "tell rahul i am late", "email Rahul the receipt": one-word recipient, then the body.
_RECIPIENT_BODY = re.compile(
    r"\b(?:notify|tell|text|message|email|mail|whatsapp|sms|ping|inform|send)\s+"
    r"(?:the\s+)?([\w\-]+)(?:\s+(?:that|about)\b)?\s*(.*)$",
    re.IGNORECASE,
)
_REMIND = re.compile(r"\bremind\s+([\w\-]+)\s+(?:to|about)\s+(.+)$", re.IGNORECASE)
_LEADING_ARTICLE = re.compile(r"^(?:the|a|an)\s+", re.IGNORECASE)
_MEETING = re.compile(r"\bmeeting\b", re.IGNORECASE)
_HELP = re.compile(
    r"^\s*(?:help|hi|hello|hey|menu|examples?|what can you do)\s*[?!.]*\s*$", re.IGNORECASE
)
HELP_TEXT = (
    "I turn messages into tasks with simple keyword rules (scripted mode). Try: "
    '"pay ₹40 to the bakery", "pay rahul 500", "tip the driver 50", '
    '"email the receipt to Rahul", "tell rahul i am late", "whatsapp asha", '
    '"book a room for friday 5pm", "rent a car for sunday", '
    '"find booking HK-204", "what is my balance", or combine two with "and".'
)

NOTHING_SCHEDULED = "Nothing was scheduled."


@dataclass
class RouteResult:
    tasks: list[dict[str, Any]] = field(default_factory=list)
    matched: list[dict[str, Any]] = field(default_factory=list)
    command: dict[str, Any] | None = None
    reply: str | None = None
    mode: str = "scripted"


def normalise(text: str) -> str:
    return text.replace("’", "'").replace("‘", "'").strip()


def classify(clause: str) -> tuple[str, str | None]:
    """First matching keyword rule wins; otherwise general."""
    for intent, kw, pattern in _KEYWORD_RULES:
        if pattern.search(clause):
            return intent, kw
    return "general", None


def _has_keyword(text: str) -> bool:
    return any(p.search(text) for _, _, p in _KEYWORD_RULES)


def split_clauses(text: str) -> list[str]:
    """Split on and/then/also/;/. only when both sides contain a keyword."""
    parts = _SPLIT.split(text)
    clauses = [parts[0]]
    for sep, piece in zip(parts[1::2], parts[2::2]):
        if piece.strip() and _has_keyword(clauses[-1]) and _has_keyword(piece):
            clauses.append(piece)
        else:
            clauses[-1] += sep + piece
    return [c.strip(" .;,") for c in clauses if c.strip(" .;,")]


def _number(raw: str) -> int | float:
    value = float(raw)
    return int(value) if value.is_integer() else value


def _first(pattern: re.Pattern[str], text: str) -> str | None:
    m = pattern.search(text)
    return m.group(1).strip() if m else None


def _message_params(clause: str) -> dict[str, str]:
    remind = _REMIND.search(clause)
    if remind:
        return {"to": remind.group(1), "body": remind.group(2).strip()}
    to = _first(_TO, clause)
    if to:
        return {"to": to, "body": _first(_BODY, clause) or clause}
    m = _RECIPIENT_BODY.search(clause)
    if not m:
        return {"to": "unknown", "body": clause}
    body = _LEADING_ARTICLE.sub("", m.group(2).strip())
    return {"to": m.group(1), "body": body or clause}


def extract_params(intent: str, clause: str) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if intent == "payment":
        for pattern in _AMOUNT:
            m = pattern.search(clause)
            if m:
                params["amount"] = _number(m.group(1))
                break
        else:
            bare = _first(_BARE_AMOUNT, _REF.sub(" ", _TIME.sub(" ", clause)))
            if bare:
                params["amount"] = _number(bare)
        params["currency"] = "INR"
        # "pay rahul 500" names the payee after the verb; "pay the bill" names none.
        after_verb = _first(_PAYEE_AFTER_VERB, clause) if "amount" in params else None
        params["payee"] = _first(_TO, clause) or after_verb or "unknown"
        params["item"] = _first(_ITEM, clause)
    elif intent == "message":
        params.update(_message_params(clause))
    elif intent == "booking":
        params["what"] = _first(_WHAT, clause) or ("meeting" if _MEETING.search(clause) else None)
        params["day"] = (_first(_DAY, clause) or "").lower() or None
        params["time"] = (_first(_TIME, clause) or "").lower() or None
        ref = _first(_REF, clause)
        params["ref"] = ref.upper() if ref else None
    elif intent == "lookup":
        params["query"] = clause
        ref = _first(_REF, clause)
        params["ref"] = ref.upper() if ref else None
    else:
        params["text"] = clause
    return {k: v for k, v in params.items() if v is not None}


def _cancel_command(clause: str) -> dict[str, Any] | None:
    if not _CANCEL.search(clause):
        return None
    ref = _TASK_REF.search(clause)
    if ref:
        return {"type": "cancel", "task_id": ref.group(0)}
    intent, kw = classify(clause)
    if kw is None:
        return None
    return {"type": "cancel", "intent": intent}


def route(text: str) -> RouteResult:
    result = RouteResult()
    if _HELP.match(normalise(text)):
        result.reply = HELP_TEXT
        return result
    negated = False
    for clause in split_clauses(normalise(text)):
        if _NEGATION.search(clause):
            negated = True
            command = _cancel_command(clause)
            if command and result.command is None:
                result.command = command
            result.matched.append(
                {"clause": clause, "intent": None, "keyword": None, "rule": "negation"}
            )
            continue
        intent, kw = classify(clause)
        depends_on = [len(result.tasks)] if result.tasks else []
        result.tasks.append(build_task(intent, extract_params(intent, clause), depends_on))
        result.matched.append(
            {
                "clause": clause,
                "intent": intent,
                "keyword": kw,
                "rule": "keyword" if kw else "default",
            }
        )
    if not result.tasks and result.command is None:
        result.reply = NOTHING_SCHEDULED if negated or not text.strip() else None
    return result
