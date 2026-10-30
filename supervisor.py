"""Supervisor: starts, kills, restarts and resets every process. See docs/SUPERVISOR.md.

    python supervisor.py --mode demo   # control API on :8009, world on 8000-8004
    python supervisor.py --mode test   # control API on :9009, world on 9000-9004

Each child runs in its own session (process group), so a kill hits only that
child, never the supervisor. /kill is a hard kill (SIGKILL on Linux/macOS,
TerminateProcess on Windows): no cleanup, like a real crash.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

import config

ROOT = Path(__file__).resolve().parent
IS_WINDOWS = os.name == "nt"
log = logging.getLogger("supervisor")

START_ORDER = ["actions", "bridge", "agent_b", "agent_a"]
STOP_ORDER = ["agent_a", "agent_b2", "agent_b", "bridge", "actions"]
PORT_FREE_TIMEOUT = 5.0
HEALTH_TIMEOUT = 15.0
STOP_GRACE = 3.0


@dataclass
class Proc:
    name: str
    port: int
    args: list[str]
    popen: subprocess.Popen | None = None
    started_at: float | None = None
    stopped_by_request: bool = False
    healthy: bool = False  # set once /health answered after the last start
    env: dict[str, str] = field(default_factory=dict)  # per-start overrides
    log_file: Any = None

    def status(self) -> str:
        if self.popen is None:
            return "stopped"
        code = self.popen.poll()
        if code is None:
            return "running" if self.healthy else "starting"
        if self.stopped_by_request or code == 0:
            return "stopped"
        return "crashed"

    def describe(self) -> dict[str, Any]:
        running = self.status() in ("running", "starting")
        return {
            "status": self.status(),
            "pid": self.popen.pid if self.popen else None,
            "port": self.port,
            "uptime_s": round(time.time() - self.started_at, 1)
            if running and self.started_at
            else None,
            "exit_code": self.popen.poll() if self.popen else None,
            "env": dict(self.env),
        }


class Supervisor:
    def __init__(self, settings: config.Settings) -> None:
        self.settings = settings
        self.lock = asyncio.Lock()
        self.world_env: dict[str, str] = {}
        s = settings
        py = [sys.executable]
        uv = [*py, "-m", "uvicorn"]
        self.procs: dict[str, Proc] = {
            "actions": Proc(
                "actions",
                s.actions_port,
                [*uv, "services.actions:app", "--host", "127.0.0.1", "--port", str(s.actions_port)],
            ),
            "bridge": Proc(
                "bridge",
                s.bridge_port,
                [*uv, "bridge.main:app", "--host", "127.0.0.1", "--port", str(s.bridge_port)],
            ),
            "agent_b": Proc(
                "agent_b",
                s.agent_b_port,
                [*py, "-m", "adapters.agent_b", "--port", str(s.agent_b_port)],
            ),
            "agent_a": Proc(
                "agent_a",
                s.agent_a_port,
                [*py, "-m", "adapters.agent_a", "--port", str(s.agent_a_port)],
            ),
            "agent_b2": Proc(
                "agent_b2",
                s.agent_b2_port,
                [*py, "-m", "adapters.agent_b", "--id", "agent_b2", "--port", str(s.agent_b2_port)],
            ),
        }

    def get(self, name: str) -> Proc:
        if name not in self.procs:
            raise HTTPException(404, f"unknown process {name}")
        return self.procs[name]

    def status(self) -> dict[str, Any]:
        return {name: p.describe() for name, p in self.procs.items()}

    # --- low-level process control ----------------------------------------------------

    def _child_env(self, proc: Proc) -> dict[str, str]:
        env = {**os.environ, **self.world_env, **proc.env}
        env.update(MODE=self.settings.mode, PYTHONUNBUFFERED="1")
        if proc.name.startswith("agent_"):
            env["AGENT_ID"] = proc.name
        return env

    def _signal(self, proc: Proc, sig: int) -> None:
        if proc.popen is None or proc.popen.poll() is not None:
            return
        if IS_WINDOWS:
            proc.popen.kill() if sig == getattr(signal, "SIGKILL", None) else proc.popen.terminate()
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.popen.pid, sig)  # the child's own group: never the supervisor

    async def _wait_exit(self, proc: Proc, timeout: float) -> bool:
        end = time.monotonic() + timeout
        while proc.popen and proc.popen.poll() is None:
            if time.monotonic() > end:
                return False
            await asyncio.sleep(0.05)
        return True

    @staticmethod
    def port_free(port: int) -> bool:
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                return False
            return True

    async def _wait_port_free(self, proc: Proc) -> None:
        end = time.monotonic() + PORT_FREE_TIMEOUT
        while not self.port_free(proc.port):
            if time.monotonic() > end:
                raise HTTPException(409, f"port {proc.port} for {proc.name} is still in use")
            await asyncio.sleep(0.05)

    async def _wait_healthy(self, proc: Proc) -> None:
        url = f"http://127.0.0.1:{proc.port}/health"
        end = time.monotonic() + HEALTH_TIMEOUT
        async with httpx.AsyncClient(timeout=0.5) as client:
            while proc.status() == "starting":
                with contextlib.suppress(httpx.HTTPError):
                    if (await client.get(url)).status_code == 200:
                        proc.healthy = True
                        return
                if time.monotonic() > end:
                    raise HTTPException(500, f"{proc.name} did not become healthy")
                await asyncio.sleep(0.05)
        # It exited during start-up (for example an immediate crash point); status shows it.

    async def _start(self, proc: Proc) -> None:
        if proc.status() in ("running", "starting"):
            return
        await self._wait_port_free(proc)
        log_dir = self.settings.log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        if proc.log_file:
            proc.log_file.close()
        # Brief blocking calls under the lock are fine for a local control API.
        proc.log_file = open(log_dir / f"{proc.name}.log", "ab")  # noqa: SIM115, ASYNC230
        proc.log_file.write(
            f"=== {time.strftime('%H:%M:%S')} start {proc.name} env={proc.env} ===\n".encode()
        )
        proc.log_file.flush()
        kwargs: dict[str, Any] = {}
        if IS_WINDOWS:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        proc.popen = subprocess.Popen(  # noqa: ASYNC220
            proc.args,
            cwd=ROOT,
            env=self._child_env(proc),
            stdout=proc.log_file,
            stderr=subprocess.STDOUT,
            **kwargs,
        )
        proc.started_at = time.time()
        proc.stopped_by_request = False
        proc.healthy = False
        log.info("started %s pid %s", proc.name, proc.popen.pid)
        await self._wait_healthy(proc)

    async def _kill(self, proc: Proc) -> None:
        self._signal(proc, signal.SIGKILL if not IS_WINDOWS else signal.SIGTERM)
        if IS_WINDOWS and proc.popen:
            proc.popen.kill()
        await self._wait_exit(proc, 5)
        log.info("killed %s", proc.name)

    async def _stop(self, proc: Proc) -> None:
        if proc.status() not in ("running", "starting"):
            return
        proc.stopped_by_request = True
        self._signal(proc, signal.SIGTERM)
        if not await self._wait_exit(proc, STOP_GRACE):
            await self._kill(proc)
        log.info("stopped %s", proc.name)

    # --- control operations ---------------------------------------------------------------

    async def start(self, name: str, env: dict[str, str] | None) -> dict[str, Any]:
        proc = self.get(name)
        async with self.lock:
            if env is not None and proc.status() not in ("running", "starting"):
                proc.env = dict(env)
            await self._start(proc)
        return proc.describe()

    async def stop(self, name: str) -> dict[str, Any]:
        proc = self.get(name)
        async with self.lock:
            await self._stop(proc)
        return proc.describe()

    async def kill(self, name: str) -> dict[str, Any]:
        proc = self.get(name)
        async with self.lock:
            await self._kill(proc)
        return proc.describe()

    async def restart(self, name: str, env: dict[str, str] | None) -> dict[str, Any]:
        proc = self.get(name)
        async with self.lock:
            await self._kill(proc)
            if env is not None:
                proc.env = dict(env)
            elif self._crash_mode(proc) != "always":
                proc.env.pop("CRASH_AT", None)  # CRASH_MODE=once: the crash point is used up
            await self._start(proc)
        return proc.describe()

    def _crash_mode(self, proc: Proc) -> str:
        return proc.env.get("CRASH_MODE") or self.world_env.get("CRASH_MODE") or "once"

    async def reset(self, keep_running: bool, env: dict[str, str]) -> dict[str, Any]:
        async with self.lock:
            for name in STOP_ORDER:  # agents first, so nobody logs a dead bridge
                await self._stop(self.procs[name])
            for proc in self.procs.values():
                await self._wait_port_free(proc)
                proc.env = {}
            self.wipe_data()
            self.world_env = dict(env)
            if keep_running:
                for name in START_ORDER:
                    await self._start(self.procs[name])
        return self.status()

    def wipe_data(self) -> None:
        data = self.settings.data_dir
        data.mkdir(parents=True, exist_ok=True)
        for item in data.iterdir():  # includes SQLite -wal and -shm files
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
        log.info("wiped %s", data)

    async def start_all(self) -> None:
        """Bring the default world up without wiping data (supervisor boot)."""
        async with self.lock:
            for name in START_ORDER:
                await self._start(self.procs[name])

    async def shutdown(self) -> None:
        for name in STOP_ORDER:
            proc = self.procs[name]
            await self._stop(proc)
            if proc.log_file:
                proc.log_file.close()

    def tail(self, name: str, lines: int) -> list[str]:
        self.get(name)
        path = self.settings.log_dir / f"{name}.log"
        if not path.exists():
            return []
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]


def create_app(settings: config.Settings, boot_world: bool = False) -> FastAPI:
    sup = Supervisor(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        booting = asyncio.create_task(sup.start_all()) if boot_world else None
        try:
            yield
        finally:
            if booting:
                booting.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await booting
            await sup.shutdown()

    app = FastAPI(title=f"Supervisor ({settings.mode} world)", lifespan=lifespan)
    app.state.supervisor = sup
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def env_of(body: dict[str, Any] | None) -> dict[str, str] | None:
        if not body or body.get("env") is None:
            return None
        return {str(k): str(v) for k, v in body["env"].items()}

    @app.get("/status")
    async def status() -> dict[str, Any]:
        return sup.status()

    @app.post("/start/{name}")
    async def start(name: str, body: dict[str, Any] | None = Body(None)) -> dict[str, Any]:
        return await sup.start(name, env_of(body))

    @app.post("/stop/{name}")
    async def stop(name: str) -> dict[str, Any]:
        return await sup.stop(name)

    @app.post("/kill/{name}")
    async def kill(name: str) -> dict[str, Any]:
        return await sup.kill(name)

    @app.post("/restart/{name}")
    async def restart(name: str, body: dict[str, Any] | None = Body(None)) -> dict[str, Any]:
        return await sup.restart(name, env_of(body))

    @app.post("/reset")
    async def reset(body: dict[str, Any] | None = Body(None)) -> dict[str, Any]:
        body = body or {}
        return await sup.reset(bool(body.get("keep_running", True)), env_of(body) or {})

    @app.get("/logs/{name}")
    async def logs(name: str, tail: int = 200) -> dict[str, Any]:
        return {"name": name, "lines": sup.tail(name, tail)}

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=["demo", "test"], default=os.environ.get("MODE", "demo"))
    parser.add_argument(
        "--no-start", action="store_true", help="only start the control API, not the world"
    )
    args = parser.parse_args()
    os.environ["MODE"] = args.mode
    settings = config.load(args.mode)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s supervisor %(message)s")
    app = create_app(settings, boot_world=not args.no_start)
    uvicorn.run(app, host="127.0.0.1", port=settings.supervisor_port, log_level="warning")


if __name__ == "__main__":
    main()
