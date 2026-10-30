"""HTTP clients used by the tests. They speak the agents' native formats through
the same codecs the adapters use, and otherwise only call public endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from bridge.codecs import a2a_style, mcp_style
from bridge.models import SCHEMA_VERSION, Envelope


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


class _Http:
    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self.base_url = base_url
        self.http = httpx.Client(base_url=base_url, timeout=timeout)

    def close(self) -> None:
        self.http.close()

    def _get(self, path: str, **params: Any) -> Any:
        r = self.http.get(path, params={k: v for k, v in params.items() if v is not None})
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: Any = None, ok: bool = True) -> Any:
        r = self.http.post(path, json=body if body is not None else {})
        if ok:
            r.raise_for_status()
        return r.json()


class SupervisorClient(_Http):
    def status(self) -> dict[str, Any]:
        return self._get("/status")

    def state(self, name: str) -> str:
        return self.status()[name]["status"]

    def reset(self, env: dict[str, str] | None = None, keep_running: bool = True):
        return self._post("/reset", {"keep_running": keep_running, "env": env or {}})

    def start(self, name: str, env: dict[str, str] | None = None):
        return self._post(f"/start/{name}", {"env": env} if env is not None else {})

    def stop(self, name: str):
        return self._post(f"/stop/{name}")

    def kill(self, name: str):
        return self._post(f"/kill/{name}")

    def restart(self, name: str, env: dict[str, str] | None = None):
        return self._post(f"/restart/{name}", {"env": env} if env is not None else {})

    def logs(self, name: str, tail: int = 200) -> list[str]:
        return self._get(f"/logs/{name}", tail=tail)["lines"]


class AgentAClient(_Http):
    def message(self, text: str, msg_id: str | None = None) -> dict[str, Any]:
        body = {"text": text}
        if msg_id is not None:
            body["msg_id"] = msg_id
        return self._post("/message", body)

    def confirm(self, task_id: str) -> dict[str, Any]:
        return self._post("/confirm", {"task_id": task_id}, ok=False)

    def cancel(self, task_id: str) -> dict[str, Any]:
        return self._post("/cancel", {"task_id": task_id}, ok=False)

    def chat(self) -> list[dict[str, Any]]:
        return self._get("/chat")

    def state(self) -> dict[str, Any]:
        return self._get("/state")


class AgentBClient(_Http):
    def pause(self):
        return self._post("/pause")

    def unpause(self):
        return self._post("/unpause")

    def claim(self, task_id: str) -> httpx.Response:
        return self.http.post("/claim", json={"task_id": task_id})

    def state(self) -> dict[str, Any]:
        return self._get("/state")


class ActionsClient(_Http):
    def perform(self, key: str, task_id: str, fence: int, action: str, params=None):
        return self.http.post(
            "/actions",
            json={
                "idempotency_key": key,
                "task_id": task_id,
                "fence": fence,
                "action": action,
                "params": params or {},
            },
        )

    def ledger(self, task_id: str | None = None, action: str | None = None):
        return self._get("/ledger", task_id=task_id, action=action)

    def count(self, task_id: str | None = None, action: str | None = None) -> int:
        return self._get("/ledger/count", task_id=task_id, action=action)["count"]

    def receipt(self, receipt_id: str) -> httpx.Response:
        return self.http.get(f"/receipts/{receipt_id}")
