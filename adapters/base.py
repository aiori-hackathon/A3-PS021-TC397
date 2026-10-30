"""Shared base for the scripted mock adapters. See docs/ADAPTERS.md#shared-base-adaptersbasepy.

- Bridge client: sends envelopes through the agent's codec. A 502/503/504,
  a timeout or a connection error means "outcome unknown": it retries with
  the SAME request_id, backoff 0.1 -> 0.2 -> 0.4 ... capped at RETRY_BACKOFF_MAX.
- Heartbeat loop: every HEARTBEAT_SECONDS, with the tasks it currently owns.
- Crash hooks: maybe_crash(point) exits the process immediately (os._exit).
- A small HTTP server: /health, /state, plus agent-specific routes.

Agents keep nothing they need after a crash: on restart they read it back
from the bridge. Request IDs are derived from durable IDs (docs/IDENTIFIERS.md);
counters are scoped by this process's boot_id.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from types import ModuleType
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import config
from bridge.models import SCHEMA_VERSION, Envelope, Reply

RETRY_STATUSES = {502, 503, 504}
QUIET_OPS = {"agent.heartbeat", "events.since"}


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class OutcomeUnknown(Exception):
    """A request whose result we cannot know (dropped, cut, timed out)."""


class Agent:
    protocol: str = ""
    codec: ModuleType
    entrance: str = ""

    def __init__(self, agent_id: str, port: int, settings: config.Settings) -> None:
        self.agent_id = agent_id
        self.port = port
        self.settings = settings
        self.boot_id = uuid.uuid4().hex[:8]  # scopes in-memory counters; never a key alone
        self.bridge_url = settings.url(settings.bridge_port)
        self.counters: dict[str, int] = defaultdict(int)
        self.owned: set[str] = set()
        self.lines: deque[str] = deque(maxlen=200)
        self.messages: deque[dict[str, Any]] = deque(maxlen=50)
        self.http: httpx.AsyncClient | None = None
        self.app = FastAPI(
            title=f"{agent_id} ({self.protocol} mock adapter)", lifespan=self._lifespan
        )
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_methods=["*"],
            allow_headers=["*"],
        )
        self.app.get("/health")(self.route_health)
        self.app.get("/state")(self.route_state)

    # --- logging and crash hooks -------------------------------------------------

    def log(self, text: str) -> None:
        line = f"{now_iso()} {self.agent_id} {text}"
        self.lines.append(line)
        print(line, flush=True)

    def maybe_crash(self, point: str) -> None:
        if self.settings.crash_at == point:
            self.log(f"CRASH_AT={point}: exiting now with no cleanup")
            os._exit(1)

    def next_id(self, kind: str) -> int:
        self.counters[kind] += 1
        return self.counters[kind]

    # --- bridge client --------------------------------------------------------------

    def encode(self, env: Envelope) -> dict[str, Any]:
        return self.codec.encode_request(env)

    async def call(
        self,
        op: str,
        request_id: str | None = None,
        *,
        task_id: str | None = None,
        dialog_id: str | None = None,
        fence: int | None = None,
        payload: dict[str, Any] | None = None,
        retry: bool = True,
    ) -> Reply:
        env = Envelope(
            schema=SCHEMA_VERSION,
            request_id=request_id,
            sender=self.agent_id,
            op=op,
            dialog_id=dialog_id,
            task_id=task_id,
            fence=fence,
            payload=payload or {},
            sent_at=now_iso(),
        )
        native = self.encode(env)
        delay = 0.1
        while True:
            try:
                r = await self.http.post(self.bridge_url + self.entrance, json=native)
                if r.status_code in RETRY_STATUSES:
                    raise OutcomeUnknown(f"HTTP {r.status_code}")
                body = r.json()
                if op not in QUIET_OPS and not (
                    op == "task.claim_next" and body.get("result") is None
                ):
                    # Keep the protocol log readable: no polls, heartbeats or empty claims.
                    self.messages.append(
                        {"ts": now_iso(), "op": op, "request": native, "reply": body}
                    )
                return self.codec.decode_reply(body)
            except (httpx.TransportError, OutcomeUnknown, ValueError) as exc:
                if not retry:
                    raise OutcomeUnknown(str(exc)) from exc
                self.log(f"{op} {request_id}: outcome unknown ({exc!r}); retrying same request_id")
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.settings.retry_backoff_max)

    async def register(self) -> None:
        reply = await self.call(
            "agent.register",
            f"{self.agent_id}:register:{self.boot_id}",
            payload={
                "protocol": self.protocol,
                "base_url": self.settings.url(self.port),
            },
        )
        self.log(f"registered with the bridge (boot {self.boot_id}): ok={reply.ok}")

    async def heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.heartbeat_seconds)
            n = self.next_id("hb")
            with suppress(OutcomeUnknown):  # a missed heartbeat is fine; the next one counts
                await self.call(
                    "agent.heartbeat",
                    f"{self.agent_id}:hb:{self.boot_id}:{n}",
                    payload={"task_ids": sorted(self.owned)},
                    retry=False,
                )

    # --- lifecycle ----------------------------------------------------------------------

    async def run(self) -> None:  # overridden
        await self.register()

    async def _guarded_run(self) -> None:
        try:
            await self.run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 (a bug: make it visible, then die like a crash)
            self.log(f"main loop failed: {exc!r}")
            os._exit(2)

    @asynccontextmanager
    async def _lifespan(self, app: FastAPI):
        self.http = httpx.AsyncClient(timeout=self.settings.http_timeout)
        self.log(
            f"starting ({self.protocol}, port {self.port}, crash_at={self.settings.crash_at}, "
            f"step_delay={self.settings.step_delay})"
        )
        loops = [
            asyncio.create_task(self._guarded_run()),
            asyncio.create_task(self.heartbeat_loop()),
        ]
        try:
            yield
        finally:
            for task in loops:
                task.cancel()
            for task in loops:
                with suppress(asyncio.CancelledError):
                    await task
            await self.http.aclose()

    async def route_health(self) -> dict[str, Any]:
        return {"ok": True, "agent_id": self.agent_id, "boot_id": self.boot_id}

    def state(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "protocol": self.protocol,
            "boot_id": self.boot_id,
            "owned": sorted(self.owned),
            "log": list(self.lines)[-50:],
            "messages": list(self.messages)[-20:],
            "time": time.time(),
        }

    async def route_state(self) -> dict[str, Any]:
        return self.state()

    def serve(self) -> None:
        uvicorn.run(self.app, host="127.0.0.1", port=self.port, log_level="warning")


def parse_args(default_id: str, port_attr: str) -> tuple[str, int, config.Settings]:
    settings = config.load()
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", default=settings.agent_id or default_id)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args(sys.argv[1:])
    port = args.port or getattr(settings, port_attr)
    return args.id, port, settings
