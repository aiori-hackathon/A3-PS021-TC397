"""FastAPI app for the Dialog Bridge: routes, WebSocket, background jobs. See docs/BRIDGE.md.

Agents talk to the bridge only through POST /mcp (MCP-style) and POST /a2a
(A2A-style). Everything else is a read-only or control route for the
dashboard, tests and supervisor.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager, suppress
from types import ModuleType
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import config
from bridge import events, jobs, metrics, registry, tasks
from bridge.codecs import a2a_style, mcp_style
from bridge.db import Database
from bridge.handlers import Bridge
from bridge.models import SCHEMA_VERSION, Reply, Task

log = logging.getLogger("bridge")

SETTINGS = config.load()
BOOT_ID = uuid.uuid4().hex[:12]
HUB = events.Hub()


class State:
    db: Database
    bridge: Bridge


state = State()


@asynccontextmanager
async def lifespan(app: FastAPI):
    HUB.bind(asyncio.get_running_loop())
    state.db = Database(SETTINGS.data_dir / "bridge.db", on_commit=HUB.notify)
    if not state.db.integrity_ok():
        raise RuntimeError(f"integrity check failed for {state.db.path}")
    state.bridge = Bridge(SETTINGS, state.db)
    background = [
        asyncio.create_task(
            jobs.every("dispatcher", SETTINGS.dispatch_tick, jobs.dispatch_once, state.db)
        ),
    ]
    try:
        yield
    finally:
        for task in background:
            task.cancel()
        for task in background:
            with suppress(asyncio.CancelledError):
                await task
        state.db.close()


app = FastAPI(
    title=f"Dialog Bridge ({SCHEMA_VERSION}, an experimental schema)",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(SETTINGS.cors_origins),
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- agent entrances ---------------------------------------------------------------


async def _entrance(request: Request, codec: ModuleType, protocol: str) -> JSONResponse:
    try:
        native = json.loads(await request.body())
    except ValueError:
        reply = Reply.failure("validation_error", "body is not valid JSON")
        return JSONResponse(codec.encode_reply(reply, {}), status_code=reply.status_code)
    status, body = await run_in_threadpool(state.bridge.handle_native, native, codec, protocol)
    return JSONResponse(body, status_code=status)


def _body_doc(example: dict[str, Any]) -> dict[str, Any]:
    """OpenAPI body so /docs offers an editable example; the route still parses the raw body."""
    return {
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": {"type": "object"}, "example": example}},
        }
    }


MCP_EXAMPLE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
        "name": "dialog.create",
        "arguments": {
            "msg_id": "msg_1",
            "text": "write me a poem",
            "tasks": [
                {
                    "intent": "general",
                    "action": None,
                    "params": {},
                    "steps": ["parse", "plan", "work", "report"],
                    "needs_confirmation": False,
                    "depends_on": [],
                }
            ],
        },
        "_meta": {
            "schema": SCHEMA_VERSION,
            "request_id": "msg_1:create",
            "sender": "agent_a",
            "sent_at": "2026-11-02T10:14:58.004Z",
        },
    },
}
A2A_EXAMPLE = {
    "message": {
        "messageId": "agent_b:claim_next:boot1:1",
        "role": "agent",
        "kind": "task.claim_next",
        "parts": [{"kind": "data", "data": {}}],
        "metadata": {
            "schema": SCHEMA_VERSION,
            "sender": "agent_b",
            "sent_at": "2026-11-02T10:15:00.000Z",
        },
    }
}


@app.post("/mcp", openapi_extra=_body_doc(MCP_EXAMPLE))
async def mcp_entrance(request: Request) -> JSONResponse:
    """MCP-style JSON-RPC tool calls (Agent A)."""
    return await _entrance(request, mcp_style, "mcp-style")


@app.post("/a2a", openapi_extra=_body_doc(A2A_EXAMPLE))
async def a2a_entrance(request: Request) -> JSONResponse:
    """A2A-style task/message requests (Agent B, B2)."""
    return await _entrance(request, a2a_style, "a2a-style")


# --- read-only routes ------------------------------------------------------------


def _read(fn, *args):
    with state.db.read() as tx:
        return fn(tx, *args)


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "boot_id": BOOT_ID, "last_seq": _read(events.last_seq)}


@app.get("/dialogs")
def list_dialogs() -> list[dict[str, Any]]:
    def query(tx):
        rows = tx.all("SELECT * FROM dialogs ORDER BY rowid")
        return [tasks.dialog_record(tx, r) for r in rows]

    return _read(query)


@app.get("/dialogs/{dialog_id}")
def get_dialog(dialog_id: str) -> dict[str, Any]:
    view = _read(tasks.dialog_view, dialog_id)
    if view is None:
        raise HTTPException(404, f"unknown dialog {dialog_id}")
    return view


def _task(task_id: str) -> dict[str, Any]:
    def query(tx):
        row = tasks.row(tx, task_id)
        return tasks.record(tx, row) if row else None

    record = _read(query)
    if record is None:
        raise HTTPException(404, f"unknown task {task_id}")
    return record


@app.get("/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, Any]:
    return _task(task_id)


@app.get("/tasks/{task_id}/views")
def task_views(task_id: str) -> dict[str, Any]:
    """The same task in shared, MCP-style and A2A-style form (inspector)."""
    task = Task.model_validate(_task(task_id))
    return {
        "shared": task.model_dump(mode="json"),
        "mcp_style": mcp_style.encode_task_view(task),
        "a2a_style": a2a_style.encode_task_view(task),
    }


@app.get("/events")
def list_events(
    since: int = 0,
    task_id: str | None = None,
    type: str | None = None,
    dialog_id: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    return _read(events.since, since, task_id, type, dialog_id, limit)


@app.get("/agents")
def list_agents() -> list[dict[str, Any]]:
    return _read(registry.all_agents)


@app.get("/metrics")
def get_metrics() -> dict[str, Any]:
    return _read(metrics.compute)


class Annotation(BaseModel):
    text: str
    test_id: str | None = None


@app.post("/annotate")
def annotate(note: Annotation) -> dict[str, Any]:
    """Adds a test_step event (captions for watch-mode tests)."""
    with state.db.transaction() as tx:
        seq = tx.event("test_step", "test", data=note.model_dump(exclude_none=True))
    return {"ok": True, "seq": seq}


# --- live event stream -----------------------------------------------------------


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    """hello on connect; {"type":"since","seq":N} replays from N; then every committed event."""
    await websocket.accept()
    wake = asyncio.Event()
    HUB.clients.add(wake)
    cursor = await run_in_threadpool(_read, events.last_seq)
    pending_since: list[int] = []
    await websocket.send_json({"type": "hello", "boot_id": BOOT_ID, "last_seq": cursor})

    async def reader() -> None:
        try:
            while True:
                msg = await websocket.receive_json()
                if isinstance(msg, dict) and msg.get("type") == "since":
                    pending_since.append(int(msg.get("seq", 0)))
                    wake.set()
        except (WebSocketDisconnect, ValueError, TypeError):
            pass
        finally:
            wake.set()

    reading = asyncio.create_task(reader())
    try:
        while not reading.done():
            await wake.wait()
            wake.clear()
            if pending_since:
                cursor = pending_since.pop()
                pending_since.clear()
            new = await run_in_threadpool(_read, events.since, cursor)
            for event in new:
                await websocket.send_json({"type": "event", "event": event})
                cursor = event["seq"]
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        HUB.clients.discard(wake)
        reading.cancel()
