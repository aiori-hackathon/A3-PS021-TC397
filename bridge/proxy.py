"""Fault-injection middleware for the bridge's agent links. See docs/FAULT_INJECTION.md.

Traffic on /mcp is the agent_a link; traffic on /a2a is the link of its
sender (agent_b or agent_b2). Rules can match on `op`. The proxy runs before
any handler, so a dropped request never reaches the inbox, while a dropped
reply belongs to a request that was fully processed and committed.
"""

from __future__ import annotations

from typing import Any

from bridge.codecs import a2a_style, mcp_style

PATHS = {"/mcp", "/a2a"}
LINKS = ("agent_a", "agent_b", "agent_b2")


def classify(path: str, native: Any) -> tuple[str | None, dict[str, Any]]:
    if path == "/mcp":
        ident = mcp_style.peek(native)
        return "agent_a", {"op": ident["op"], "request_id": ident["request_id"]}
    ident = a2a_style.peek(native)
    sender = ident["sender"] if ident["sender"] in LINKS else None
    return sender, {"op": ident["op"], "request_id": ident["request_id"]}
