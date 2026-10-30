"""Agent A: orchestrator, MCP-style mock adapter. See docs/ADAPTERS.md#agent-a-adaptersagent_apy.

Turns user text into tasks with the rule-based intent router, creates the
dialog through the bridge, asks for (or auto-gives) confirmation of
side-effect tasks, and follows the event log to tell the user what happened.
It never performs a side effect. The chat view is rebuilt from the bridge's
events after a restart. The only local file, data/<mode>/agent_a.json, holds
the msg_id counter and the user messages whose dialog.create may not have
completed; after a restart they are retried with the same {msg_id}:create,
so the inbox returns the existing dialog instead of creating a second one.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from fastapi import Body
from fastapi.responses import JSONResponse

from adapters.base import Agent, parse_args
from adapters.router import route
from bridge.codecs import mcp_style
from bridge.models import ERROR_HTTP_STATUS, Envelope

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}
EVENTS_PAGE = 1000


def money(params: dict[str, Any]) -> str | None:
    amount = params.get("amount")
    return f"₹{amount}" if amount is not None else None


def describe(intent: str, params: dict[str, Any]) -> str:
    if intent == "payment":
        return f"pay {money(params) or 'an unspecified amount'} to {params.get('payee', 'unknown')}"
    if intent == "message":
        return f"send '{params.get('body', '')}' to {params.get('to', 'unknown')}"
    if intent == "booking":
        parts = [params.get(k) for k in ("what", "day", "time")]
        return "book " + " ".join(p for p in parts if p) if any(parts) else "make a booking"
    if intent == "lookup":
        return f"look up {params.get('ref') or params.get('query', '')}"
    return f"work on '{params.get('text', '')}'"


def detected(intent: str, params: dict[str, Any]) -> str:
    keys = {
        "payment": [money(params), params.get("payee")],
        "message": [params.get("to")],
        "booking": [params.get("what"), params.get("day"), params.get("time")],
        "lookup": [params.get("ref")],
    }.get(intent, [])
    shown = " · ".join([intent, *[k for k in keys if k]])
    return f"Detected intent: {shown} · scripted mode"


class AgentA(Agent):
    protocol = "mcp-style"
    codec = mcp_style
    entrance = "/mcp"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.state_file = self.settings.data_dir / "agent_a.json"
        self.cursor = 0
        self.dialogs: dict[str, dict[str, Any]] = {}
        self.tasks: dict[str, dict[str, Any]] = {}
        self.chat: dict[tuple, dict[str, Any]] = {}
        self.in_flight: set[str] = set()  # msg_ids whose /message call is still running
        self.app.post("/message")(self.route_message)
        self.app.post("/confirm")(self.route_confirm)
        self.app.post("/cancel")(self.route_cancel)
        self.app.get("/chat")(self.route_chat)

    def encode(self, env: Envelope) -> dict[str, Any]:
        return mcp_style.encode_request(env, rpc_id=self.next_id("rpc"))

    # --- the one local file: msg_id counter + pending user messages ---------------------

    def _load(self) -> dict[str, Any]:
        data: dict[str, Any] = {"counter": 0, "pending": {}}
        if self.state_file.exists():
            data.update(json.loads(self.state_file.read_text(encoding="utf-8")))
        return data

    def _save(self, data: dict[str, Any]) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, self.state_file)  # atomic: a crash leaves the old or the new file

    def new_msg_id(self) -> str:
        data = self._load()
        data["counter"] += 1
        self._save(data)
        return f"msg_{data['counter']}"

    def remember_pending(self, msg_id: str, text: str) -> None:
        data = self._load()
        data["pending"][msg_id] = text
        self._save(data)

    def forget_pending(self, msg_id: str) -> None:
        data = self._load()
        if data["pending"].pop(msg_id, None) is not None:
            self._save(data)

    async def confirm_all(self, tasks: list[dict[str, Any]]) -> None:
        if not self.settings.auto_confirm:
            return
        for t in tasks:
            if t["needs_confirmation"]:
                await self.call("task.confirm", f"{t['task_id']}:confirm", task_id=t["task_id"])

    async def retry_pending(self) -> None:
        """After a restart: finish any dialog.create that may not have completed."""
        for msg_id, text in self._load()["pending"].items():
            routed = route(text)  # deterministic: the same tasks as before the crash
            reply = await self.call(
                "dialog.create",
                f"{msg_id}:create",
                payload={"msg_id": msg_id, "text": text, "tasks": routed.tasks},
            )
            self.log(f"retried create for {msg_id} after restart: ok={reply.ok}")
            if reply.ok:
                await self.confirm_all(reply.result["tasks"])
            self.forget_pending(msg_id)

    # --- chat --------------------------------------------------------------------------

    def say(self, key: tuple, kind: str, text: str, **extra: Any) -> None:
        if key not in self.chat:
            role = "user" if kind == "user" else "bridge" if kind == "notice" else "agent"
            self.chat[key] = {"kind": kind, "role": role, "text": text, **extra}

    def plan_text(self, dialog_id: str) -> str:
        ids = self.dialogs[dialog_id]["task_ids"]
        lines = [f"Detected {len(ids)} task(s) in dialog {dialog_id}:"]
        for task_id in ids:
            t = self.tasks[task_id]
            wait = f" (waits for task {t['index'] - 1})" if t["depends_on"] else ""
            lines.append(
                f"{t['index']}. {detected(t['intent'], t['params'])}: "
                f"{describe(t['intent'], t['params'])}{wait}"
            )
        needs = [self.tasks[i] for i in ids if self.tasks[i]["needs_confirmation"]]
        if needs and self.settings.auto_confirm:
            lines.append("Side-effect tasks confirmed automatically (AUTO_CONFIRM=1).")
        for t in needs if not self.settings.auto_confirm else []:
            lines.append(
                f"Confirm task {t['index']} ({describe(t['intent'], t['params'])})? "
                f"Confirm or Cancel {t['task_id']}."
            )
        return "\n".join(lines)

    def summary_text(self, dialog_id: str) -> str:
        parts = []
        for task_id in self.dialogs[dialog_id]["task_ids"]:
            t = self.tasks[task_id]
            outcome = t["state"].lower()
            if t.get("receipt_id"):
                outcome += f" (receipt {t['receipt_id']})"
            elif t.get("reason"):
                outcome += f" ({t['reason']})"
            parts.append(f"{describe(t['intent'], t['params'])}: {outcome}")
        return "Finished: " + "; ".join(parts) + "."

    # --- following the event log ---------------------------------------------------------

    async def run(self) -> None:
        await self.register()
        await self.retry_pending()
        while True:
            reply = await self.call("events.since", payload={"seq": self.cursor})
            batch = reply.result["events"] if reply.ok else []
            for event in batch:
                self.on_event(event)
                self.cursor = event["seq"]
            if len(batch) < EVENTS_PAGE:
                await asyncio.sleep(self.settings.poll_interval)

    def on_event(self, e: dict[str, Any]) -> None:
        """Idempotent: replaying the whole log after a restart rebuilds the same view."""
        kind, data, dialog_id, task_id = e["type"], e["data"], e["dialog_id"], e["task_id"]
        if kind == "dialog_created":
            self.dialogs.setdefault(
                dialog_id,
                {"msg_id": data["msg_id"], "text": data["text"], "task_ids": data["task_ids"]},
            )
            if data["msg_id"] not in self.in_flight:
                self.say(
                    ("user", dialog_id), "user", data["text"], dialog_id=dialog_id, seq=e["seq"]
                )
        elif kind == "task_created":
            self.tasks.setdefault(
                task_id,
                {
                    "task_id": task_id,
                    "dialog_id": dialog_id,
                    "index": data["index"],
                    "intent": data["intent"],
                    "action": data["action"],
                    "params": data["params"],
                    "depends_on": data["depends_on"],
                    "needs_confirmation": data["needs_confirmation"],
                    "state": "CREATED",
                },
            )
            d = self.dialogs.get(dialog_id)
            if (
                d
                and d["msg_id"] not in self.in_flight
                and all(i in self.tasks for i in d["task_ids"])
            ):
                self.say(
                    ("plan", dialog_id), "plan", self.plan_text(dialog_id), dialog_id=dialog_id
                )
        elif task_id in self.tasks:
            t = self.tasks[task_id]
            if kind == "state_changed":
                t["state"] = data["to"]
                if data.get("reason"):
                    t["reason"] = data["reason"]
            elif kind == "effect_committed":
                t["receipt_id"] = data["receipt_id"]
            elif kind == "lease_expired":
                extra = (
                    " The side effect may already have happened; it will only be retried with"
                    " the same key, which the actions service recognises."
                    if data.get("from") == "EFFECT_PENDING"
                    else ""
                )
                self.say(
                    ("notice", e["seq"]),
                    "notice",
                    f"{data.get('owner')} stopped responding during {data.get('from')}. "
                    f"Task {t['index']} suspended.{extra}",
                    dialog_id=dialog_id,
                    task_id=task_id,
                )
            elif kind in ("resumed", "reclaimed"):
                self.say(
                    ("notice", e["seq"]),
                    "notice",
                    f"Task {t['index']} {kind} by {e['actor']} (fence {data.get('fence')}).",
                    dialog_id=dialog_id,
                    task_id=task_id,
                )
            elif kind in ("completed", "failed", "cancelled"):
                text = f"Task {t['index']} {kind}: {describe(t['intent'], t['params'])}"
                if kind != "completed" and data.get("reason"):
                    text += f" ({data['reason']})"
                self.say(
                    ("outcome", task_id), "outcome", text, dialog_id=dialog_id, task_id=task_id
                )
                d = self.dialogs.get(dialog_id)
                if d and all(self.tasks.get(i, {}).get("state") in TERMINAL for i in d["task_ids"]):
                    self.say(
                        ("summary", dialog_id),
                        "summary",
                        self.summary_text(dialog_id),
                        dialog_id=dialog_id,
                    )
        if kind == "bridge_started" and data.get("previous_last_seq"):
            self.say(
                ("notice", e["seq"]),
                "notice",
                f"Bridge restarted: recovered {data.get('recovered_tasks')} open task(s).",
            )

    # --- routes ---------------------------------------------------------------------

    async def route_message(self, body: dict[str, Any] = Body(...)) -> JSONResponse:
        text = str(body.get("text", ""))
        msg_id = body.get("msg_id") or self.new_msg_id()
        routed = route(text)
        out: dict[str, Any] = {
            "msg_id": msg_id,
            "dialog_id": None,
            "task_ids": [],
            "tasks": routed.tasks,
            "matched": routed.matched,
            "command": routed.command,
            "mode": routed.mode,
        }
        self.say(("user-msg", msg_id), "user", text, msg_id=msg_id)
        if routed.command:
            out.update(await self.cancel_command(routed.command))
            self.say(("reply", msg_id), "agent", out["reply"], msg_id=msg_id)
            return JSONResponse(out)
        if not routed.tasks:
            out["reply"] = routed.reply or "Nothing was scheduled."
            self.say(("reply", msg_id), "agent", out["reply"], msg_id=msg_id)
            return JSONResponse(out)

        self.in_flight.add(msg_id)
        try:
            self.remember_pending(msg_id, text)
            self.maybe_crash("before_create")
            reply = await self.call(
                "dialog.create",
                f"{msg_id}:create",
                payload={"msg_id": msg_id, "text": text, "tasks": routed.tasks},
            )
            self.maybe_crash("after_create")
            if not reply.ok:
                self.forget_pending(msg_id)
                out["reply"] = f"The bridge refused the request: {reply.error.message}"
                return JSONResponse(
                    {**out, "error": reply.error.model_dump(mode="json")},
                    status_code=ERROR_HTTP_STATUS[reply.error.code],
                )
            dialog_id = reply.result["dialog_id"]
            self.dialogs.setdefault(
                dialog_id, {"msg_id": msg_id, "text": text, "task_ids": reply.result["task_ids"]}
            )
            for t in reply.result["tasks"]:
                self.tasks.setdefault(
                    t["task_id"],
                    {
                        k: t[k]
                        for k in (
                            "task_id",
                            "dialog_id",
                            "index",
                            "intent",
                            "action",
                            "params",
                            "depends_on",
                            "needs_confirmation",
                            "state",
                        )
                    },
                )
            out.update(dialog_id=dialog_id, task_ids=reply.result["task_ids"])
            out["reply"] = self.plan_text(dialog_id)
            self.chat.pop(("user-msg", msg_id), None)
            self.say(("user", dialog_id), "user", text, dialog_id=dialog_id, msg_id=msg_id)
            self.say(("plan", dialog_id), "plan", out["reply"], dialog_id=dialog_id)
        finally:
            self.in_flight.discard(msg_id)
        await self.confirm_all(reply.result["tasks"])
        self.forget_pending(msg_id)
        return JSONResponse(out)

    async def cancel_command(self, command: dict[str, Any]) -> dict[str, Any]:
        task_id = command.get("task_id")
        if task_id is None:
            open_tasks = [
                t
                for t in self.tasks.values()
                if t["intent"] == command.get("intent") and t["state"] not in TERMINAL
            ]
            task_id = open_tasks[-1]["task_id"] if open_tasks else None
        if task_id is None:
            return {
                "reply": f"There is no open {command.get('intent')} task to cancel.",
                "cancelled": None,
            }
        reply = await self.call(
            "task.cancel", f"{task_id}:cancel", task_id=task_id, payload={"reason": "user"}
        )
        if reply.ok:
            return {"reply": f"Cancelled task {task_id}.", "cancelled": task_id}
        return {
            "reply": f"Could not cancel {task_id}: {reply.error.message}",
            "cancelled": None,
            "error": reply.error.model_dump(mode="json"),
        }

    async def _task_call(self, op: str, suffix: str, body: dict[str, Any]) -> JSONResponse:
        task_id = body["task_id"]
        reply = await self.call(op, f"{task_id}:{suffix}", task_id=task_id)
        if reply.ok:
            return JSONResponse({"ok": True, "task": reply.result})
        return JSONResponse(
            {"ok": False, "error": reply.error.model_dump(mode="json")},
            status_code=ERROR_HTTP_STATUS[reply.error.code],
        )

    async def route_confirm(self, body: dict[str, Any] = Body(...)) -> JSONResponse:
        return await self._task_call("task.confirm", "confirm", body)

    async def route_cancel(self, body: dict[str, Any] = Body(...)) -> JSONResponse:
        return await self._task_call("task.cancel", "cancel", body)

    async def route_chat(self) -> list[dict[str, Any]]:
        return list(self.chat.values())

    def state(self) -> dict[str, Any]:
        return {
            **super().state(),
            "cursor": self.cursor,
            "auto_confirm": self.settings.auto_confirm,
        }


def main() -> None:
    agent_id, port, settings = parse_args("agent_a", "agent_a_port")
    AgentA(agent_id, port, settings).serve()


if __name__ == "__main__":
    main()
