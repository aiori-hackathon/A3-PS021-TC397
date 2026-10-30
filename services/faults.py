"""Shared fault-rule engine used by the bridge proxy and the actions service.
See docs/FAULT_INJECTION.md.

Rules live in memory and disappear when the process restarts. The
middleware is plain ASGI (not BaseHTTPMiddleware), so a delayed request is
still handled after its client gave up waiting, the way a slow network
delivers a late message.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

Link = Literal["agent_a", "agent_b", "agent_b2", "actions"]
FaultAction = Literal["drop_request", "drop_reply", "delay", "duplicate", "cut"]

X_FAULT = {"drop_request": "dropped", "drop_reply": "reply-dropped", "cut": "link-cut"}


class FaultRule(BaseModel):
    id: str | None = None
    link: Link
    action: FaultAction
    match: dict[str, str] = Field(default_factory=dict)
    count: int = 1
    delay_ms: int = Field(default=0, ge=0)
    note: str = ""

    @field_validator("count")
    @classmethod
    def _count(cls, value: int) -> int:
        if value == 0 or value < -1:
            raise ValueError("count is a positive number of firings, or -1 for until deleted")
        return value


class FaultEngine:
    def __init__(self, links: tuple[str, ...]) -> None:
        self.links = links
        self.rules: list[FaultRule] = []
        self.lock = threading.Lock()
        self._next = 0

    def add(self, rule: FaultRule) -> dict[str, Any]:
        if rule.link not in self.links:
            raise ValueError(f"this service handles links {list(self.links)}, not {rule.link!r}")
        with self.lock:
            self._next += 1
            stored = rule.model_copy(update={"id": f"r{self._next}"})
            self.rules.append(stored)
            return stored.model_dump()

    def list(self) -> list[dict[str, Any]]:
        with self.lock:
            return [r.model_dump() for r in self.rules]

    def delete(self, rule_id: str) -> bool:
        with self.lock:
            before = len(self.rules)
            self.rules = [r for r in self.rules if r.id != rule_id]
            return len(self.rules) < before

    def clear(self) -> int:
        with self.lock:
            n = len(self.rules)
            self.rules = []
            return n

    def take(self, link: str | None, attrs: dict[str, Any]) -> FaultRule | None:
        """The first matching rule for this request, with one firing used up."""
        if link is None:
            return None
        with self.lock:
            for i, rule in enumerate(self.rules):
                if rule.link != link:
                    continue
                if any(attrs.get(k) != v for k, v in rule.match.items()):
                    continue
                if rule.count > 0:
                    left = rule.count - 1
                    if left == 0:
                        del self.rules[i]
                    else:
                        self.rules[i] = rule.model_copy(update={"count": left})
                return rule
        return None


# (path, parsed JSON body) -> (link, attrs) for requests that can be faulted, else None.
Classifier = Callable[[str, Any], "tuple[str | None, dict[str, Any]] | None"]
OnApplied = Callable[[FaultRule, str | None, dict[str, Any]], Awaitable[None]]


class FaultMiddleware:
    def __init__(
        self,
        app,
        engine: FaultEngine,
        paths: set[str],
        classify: Classifier,
        on_applied: OnApplied | None = None,
    ) -> None:
        self.app = app
        self.engine = engine
        self.paths = paths
        self.classify = classify
        self.on_applied = on_applied

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] not in self.paths:
            await self.app(scope, receive, send)
            return
        chunks = []
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            chunks.append(message.get("body", b""))
            if not message.get("more_body"):
                break
        body = b"".join(chunks)
        try:
            parsed = json.loads(body)
        except ValueError:
            parsed = None
        link, attrs = self.classify(scope["path"], parsed) or (None, {})
        rule = self.engine.take(link, attrs)

        def replay():
            sent = False

            async def again():
                nonlocal sent
                if not sent:
                    sent = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()

            return again

        if rule is None:
            await self.app(scope, replay(), send)
            return
        if self.on_applied:
            await self.on_applied(rule, link, attrs)
        if rule.action in ("drop_request", "cut"):
            await self._refuse(send, rule)
        elif rule.action == "delay":
            await asyncio.sleep(rule.delay_ms / 1000)
            await self.app(scope, replay(), send)
        elif rule.action == "drop_reply":
            await self.app(scope, replay(), _discard)  # runs and commits
            await self._refuse(send, rule)
        elif rule.action == "duplicate":
            await self.app(scope, replay(), _discard)
            await self.app(scope, replay(), send)  # the second copy's reply is what arrives

    @staticmethod
    async def _refuse(send, rule: FaultRule) -> None:
        body = json.dumps(
            {
                "error": {
                    "code": "fault_injected",
                    "message": f"fault rule {rule.id}: {rule.action}",
                    "details": {"rule_id": rule.id, "note": rule.note},
                }
            }
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 503,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"x-fault", X_FAULT[rule.action].encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


async def _discard(message) -> None:
    return None


def fault_routes(
    engine: FaultEngine,
    on_change: Callable[[str, dict[str, Any]], None] | None = None,
) -> APIRouter:
    """POST/GET/DELETE /faults, the same on the bridge and the actions service."""
    router = APIRouter()

    def changed(what: str, data: dict[str, Any]) -> None:
        if on_change:
            on_change(what, data)

    @router.post("/faults")
    def add_fault(rule: FaultRule) -> dict[str, Any]:
        try:
            stored = engine.add(rule)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        changed("add", stored)
        return stored

    @router.get("/faults")
    def list_faults() -> list[dict[str, Any]]:
        return engine.list()

    @router.delete("/faults/{rule_id}")
    def delete_fault(rule_id: str) -> dict[str, Any]:
        if not engine.delete(rule_id):
            raise HTTPException(404, f"no fault rule {rule_id}")
        changed("delete", {"id": rule_id})
        return {"deleted": rule_id}

    @router.delete("/faults")
    def clear_faults() -> dict[str, Any]:
        n = engine.clear()
        changed("clear", {"deleted": n})
        return {"deleted": n}

    return router
