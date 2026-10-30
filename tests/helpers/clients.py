"""HTTP clients used by the tests. They speak the agents' native formats through
the same codecs the adapters use, and otherwise only call public endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from bridge.codecs import a2a_style, mcp_style
from bridge.models import SCHEMA_VERSION, Envelope, Reply


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class BridgeClient:
    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self.base_url = base_url
        self.http = httpx.Client(base_url=base_url, timeout=timeout)
        self._rpc_id = 0

    def close(self) -> None:
        self.http.close()

    # --- native entrances ---------------------------------------------------

    def envelope(self, op: str, sender: str, request_id: str | None = None, **kw: Any) -> Envelope:
        return Envelope(
            schema=kw.pop("schema", SCHEMA_VERSION),
            request_id=request_id,
            sender=sender,
            op=op,
            payload=kw.pop("payload", {}),
            sent_at=kw.pop("sent_at", now_iso()),
            **kw,
        )

    def mcp(self, op: str, request_id: str | None = None, sender: str = "agent_a", **kw: Any):
        """Send through POST /mcp. Returns (http_status, Reply)."""
        self._rpc_id += 1
        native = mcp_style.encode_request(
            self.envelope(op, sender, request_id, **kw), rpc_id=self._rpc_id
        )
        return self.mcp_native(native)

    def mcp_native(self, native: dict[str, Any]):
        r = self.http.post("/mcp", json=native)
        return r.status_code, mcp_style.decode_reply(r.json())

    def a2a(self, op: str, request_id: str | None = None, sender: str = "agent_b", **kw: Any):
        """Send through POST /a2a. Returns (http_status, Reply)."""
        native = a2a_style.encode_request(self.envelope(op, sender, request_id, **kw))
        return self.a2a_native(native)

    def a2a_native(self, native: dict[str, Any]):
        r = self.http.post("/a2a", json=native)
        return r.status_code, a2a_style.decode_reply(r.json())

    # --- read-only routes ---------------------------------------------------

    def get(self, path: str, **params: Any) -> Any:
        r = self.http.get(path, params={k: v for k, v in params.items() if v is not None})
        r.raise_for_status()
        return r.json()

    def health(self) -> dict[str, Any]:
        return self.get("/health")

    def task(self, task_id: str) -> dict[str, Any]:
        return self.get(f"/tasks/{task_id}")

    def dialog(self, dialog_id: str) -> dict[str, Any]:
        return self.get(f"/dialogs/{dialog_id}")

    def events(self, since: int = 0, task_id: str | None = None, type: str | None = None):
        return self.get("/events", since=since, task_id=task_id, type=type)

    def event_types(self, **filters: Any) -> list[str]:
        return [e["type"] for e in self.events(**filters)]


__all__ = ["BridgeClient", "Reply", "now_iso"]
