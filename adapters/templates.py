"""Step templates per intent. See docs/INTENT_ROUTING.md.

The step named after the action is the side-effect step; a task has at most
one. needs_confirmation is true exactly when there is a side effect.
"""

from __future__ import annotations

from typing import Any

TEMPLATES: dict[str, dict[str, Any]] = {
    "lookup": {
        "action": None,
        "steps": ["parse", "search", "summarise", "report"],
        "needs_confirmation": False,
    },
    "payment": {
        "action": "pay",
        "steps": ["parse", "plan", "pay", "verify", "report"],
        "needs_confirmation": True,
    },
    "message": {
        "action": "send",
        "steps": ["parse", "draft", "send", "report"],
        "needs_confirmation": True,
    },
    "booking": {
        "action": "reserve",
        "steps": ["parse", "check_slot", "reserve", "report"],
        "needs_confirmation": True,
    },
    "general": {
        "action": None,
        "steps": ["parse", "plan", "work", "report"],
        "needs_confirmation": False,
    },
}


def build_task(intent: str, params: dict[str, Any], depends_on: list[int]) -> dict[str, Any]:
    tpl = TEMPLATES[intent]
    return {
        "intent": intent,
        "action": tpl["action"],
        "params": params,
        "steps": list(tpl["steps"]),
        "needs_confirmation": tpl["needs_confirmation"],
        "depends_on": depends_on,
    }
