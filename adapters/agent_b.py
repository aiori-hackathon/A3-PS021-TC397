"""Agent B: worker, A2A-style mock adapter. See docs/ADAPTERS.md#agent-b-adaptersagent_bpy.

Scripted and deterministic: claims a task, runs its steps with a checkpoint
after each, and performs the task's one side effect through the actions
service with key {task_id}:{action}. On restart it registers, resumes the
tasks it owned and continues from the last checkpoint. Run a second worker
with `python -m adapters.agent_b --id agent_b2 --port 8004`.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
from fastapi import Body
from fastapi.responses import JSONResponse

from adapters.base import RETRY_STATUSES, Agent, parse_args
from bridge.codecs import a2a_style
from bridge.models import ERROR_HTTP_STATUS

MOCK_DATA = json.loads((Path(__file__).parent / "mock_data.json").read_text(encoding="utf-8"))
LOST = {"stale_fence", "not_owner", "invalid_transition"}


class LostTask(Exception):
    """Someone else owns the task now (or it moved on): stop working on it."""


class AgentB(Agent):
    protocol = "a2a-style"
    codec = a2a_style
    entrance = "/a2a"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.paused = False
        self.current: dict[str, Any] | None = None
        self.actions_url = self.settings.url(self.settings.actions_port)
        self.background: set[asyncio.Task] = set()
        self.app.post("/pause")(self.route_pause)
        self.app.post("/unpause")(self.route_unpause)
        self.app.post("/claim")(self.route_claim)

    # --- main loop -----------------------------------------------------------------

    async def run(self) -> None:
        await self.register()
        reply = await self.call("task.resume", f"{self.agent_id}:resume:{self.boot_id}")
        resumed = reply.result["tasks"] if reply.ok else []
        self.log(f"resume: {len(resumed)} task(s) to continue")
        for task in resumed:
            await self.work_on(task)
        while True:
            if self.paused:
                await asyncio.sleep(self.settings.poll_interval)
                continue
            n = self.next_id("claim_next")
            reply = await self.call(
                "task.claim_next", f"{self.agent_id}:claim_next:{self.boot_id}:{n}"
            )
            if reply.ok and reply.result:
                await self.work_on(reply.result)
            else:
                await asyncio.sleep(self.settings.poll_interval)

    async def work_on(self, task: dict[str, Any]) -> None:
        task_id = task["task_id"]
        self.owned.add(task_id)
        cp = task["last_checkpoint"]
        start = cp["step_index"] + 1 if cp else 0
        self.log(
            f"working on {task_id} ({task['intent']}) from step {start}, "
            f"fence {task['fence']}, attempt {task['attempt']}"
        )
        try:
            for i in range(start, len(task["steps"])):
                step = task["steps"][i]
                self.current = {
                    "task_id": task_id,
                    "step": step,
                    "index": i,
                    "fence": task["fence"],
                }
                self.maybe_crash(f"before_{step}")
                if step == task["action"]:
                    task, output = await self.do_effect(task)
                else:
                    output = await self.run_step(step, task)
                self.maybe_crash(f"after_{step}")
                task = await self.write(
                    task,
                    "task.checkpoint",
                    f"checkpoint:{i}",
                    {"step_index": i, "step": step, "output": output},
                )
            result = {"steps": len(task["steps"]), "receipt_id": task["effect"]["receipt_id"]}
            await self.write(task, "task.complete", "complete", {"result": result})
            self.log(f"completed {task_id}")
        except LostTask as exc:
            self.log(f"stopped working on {task_id}: {exc}")
        finally:
            self.owned.discard(task_id)
            self.current = None

    async def write(
        self, task: dict[str, Any], op: str, suffix: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """A worker write: {task_id}:{suffix}:{fence}, always carrying the fence."""
        fence = task["fence"]
        reply = await self.call(
            op,
            f"{task['task_id']}:{suffix}:{fence}",
            task_id=task["task_id"],
            dialog_id=task["dialog_id"],
            fence=fence,
            payload=payload,
        )
        if reply.ok:
            return reply.result
        raise LostTask(f"{op} refused: {reply.error.code} ({reply.error.message})")

    # --- the side-effect step ---------------------------------------------------------

    async def do_effect(self, task: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        key = f"{task['task_id']}:{task['action']}"  # same key on every attempt
        if task["effect"]["status"] == "committed":
            return task, {"receipt_id": task["effect"]["receipt_id"], "replayed": False}
        task = await self.write(
            task,
            "task.effect_intent",
            "effect_intent",
            {"effect_key": key, "action": task["action"], "params": task["params"]},
        )
        receipt = await self.perform(key, task)
        self.maybe_crash(f"after_{task['action']}_call")  # done, but the bridge does not know yet
        task = await self.write(
            task,
            "task.effect_result",
            "effect_result",
            {"receipt_id": receipt["receipt_id"], "replayed": receipt["replayed"]},
        )
        return task, {"receipt_id": receipt["receipt_id"], "replayed": receipt["replayed"]}

    async def perform(self, key: str, task: dict[str, Any]) -> dict[str, Any]:
        """POST /actions; on any unknown outcome retry with the same key. Never a new key."""
        body = {
            "idempotency_key": key,
            "task_id": task["task_id"],
            "fence": task["fence"],
            "action": task["action"],
            "params": task["params"],
        }
        delay = 0.1
        while True:
            try:
                r = await self.http.post(f"{self.actions_url}/actions", json=body)
                if r.status_code in RETRY_STATUSES:
                    raise httpx.TransportError(f"HTTP {r.status_code}")
            except httpx.TransportError as exc:
                self.log(f"actions {key}: outcome unknown ({exc!r}); retrying same key")
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.settings.retry_backoff_max)
                continue
            if r.status_code == 200:
                receipt = r.json()
                self.log(
                    f"actions {key}: receipt {receipt['receipt_id']} replayed={receipt['replayed']}"
                )
                return receipt
            raise LostTask(f"actions refused {key}: HTTP {r.status_code} {r.text[:200]}")

    # --- scripted steps -------------------------------------------------------------

    async def run_step(self, step: str, task: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(self.settings.step_delay)
        params = task["params"]
        if step == "parse":
            return {"intent": task["intent"], "params": params}
        if step == "plan":
            return {"steps": task["steps"]}
        if step == "search":
            ref = params.get("ref")
            found = MOCK_DATA["bookings"].get(ref) if ref else MOCK_DATA["payments"]
            return {"query": params.get("query"), "ref": ref, "found": found}
        if step == "summarise":
            return {"summary": f"{task['intent']} for {params.get('ref') or params.get('query')}"}
        if step == "draft":
            return {"draft": f"To {params.get('to')}: {params.get('body')}"}
        if step == "check_slot":
            return {"available": True, "day": params.get("day"), "time": params.get("time")}
        if step == "verify":
            receipt_id = task["effect"]["receipt_id"]
            r = await self.http.get(f"{self.actions_url}/receipts/{receipt_id}")
            return {"receipt_id": receipt_id, "verified": r.status_code == 200}
        if step == "report":
            return {"report": f"{task['intent']} done", "receipt_id": task["effect"]["receipt_id"]}
        return {"done": True}

    # --- routes ---------------------------------------------------------------------

    async def route_pause(self) -> dict[str, Any]:
        self.paused = True
        self.log("paused: claim loop stopped (heartbeats continue)")
        return {"paused": True}

    async def route_unpause(self) -> dict[str, Any]:
        self.paused = False
        self.log("unpaused")
        return {"paused": False}

    async def route_claim(self, body: dict[str, Any] = Body(...)) -> JSONResponse:
        """Claim one specific task now and work on it (used by the race test S2)."""
        task_id = body["task_id"]
        reply = await self.call(
            "task.claim", f"{self.agent_id}:claim:{task_id}:{self.boot_id}", task_id=task_id
        )
        if not reply.ok:
            self.log(f"claim {task_id} refused: {reply.error.code}")
            return JSONResponse(
                {"ok": False, "error": reply.error.model_dump(mode="json")},
                status_code=ERROR_HTTP_STATUS[reply.error.code],
            )
        job = asyncio.create_task(self.work_on(reply.result))
        self.background.add(job)
        job.add_done_callback(self.background.discard)
        return JSONResponse({"ok": True, "task": reply.result})

    def state(self) -> dict[str, Any]:
        return {**super().state(), "paused": self.paused, "current": self.current}


def main() -> None:
    agent_id, port, settings = parse_args("agent_b", "agent_b_port")
    if agent_id == "agent_b2" and port == settings.agent_b_port:
        port = settings.agent_b2_port
    AgentB(agent_id, port, settings).serve()


if __name__ == "__main__":
    main()
