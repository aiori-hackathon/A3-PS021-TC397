"""Fixtures. See docs/TESTING.md.

Phase 2 fixtures start a bare bridge process (no supervisor yet) on a free
port with its own data folder. Phase 3 adds the supervisor-driven `world`.
"""

from __future__ import annotations

import itertools
import os
import socket
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

import config
from tests.helpers.clients import BridgeClient
from tests.helpers.wait import wait_until

ROOT = Path(__file__).resolve().parents[1]
_ids = itertools.count(1)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _healthy(url: str) -> bool:
    try:
        return httpx.get(f"{url}/health", timeout=0.5).json().get("ok") is True
    except (httpx.HTTPError, ValueError):
        return False


def start_bridge(data_dir: Path, log_path: Path, extra_env: dict[str, str] | None = None):
    port = free_port()
    env = {**os.environ, "MODE": "test", "DATA_DIR": str(data_dir), **(extra_env or {})}
    log = open(log_path, "ab")  # noqa: SIM115 (closed by stop_bridge)
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "bridge.main:app", "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        wait_until(lambda: proc.poll() is not None or _healthy(url), timeout=10)
    except TimeoutError:
        stop_bridge(proc, log)
        raise
    if proc.poll() is not None:
        log.close()
        raise RuntimeError(f"bridge exited with {proc.returncode}; see {log_path}")
    return proc, log, url


def stop_bridge(proc: subprocess.Popen, log) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    log.close()


@pytest.fixture(scope="session")
def settings() -> config.Settings:
    return config.load("test")


@pytest.fixture(scope="session")
def bridge(tmp_path_factory) -> Iterator[BridgeClient]:
    """One bridge for the whole session. Tests must use their own IDs."""
    data = tmp_path_factory.mktemp("bridge")
    proc, log, url = start_bridge(data, data / "bridge.log")
    client = BridgeClient(url)
    yield client
    client.close()
    stop_bridge(proc, log)


@pytest.fixture
def fresh_bridge(tmp_path) -> Iterator[BridgeClient]:
    """A private, empty bridge for tests that look at global state (claim_next)."""
    proc, log, url = start_bridge(tmp_path, tmp_path / "bridge.log")
    client = BridgeClient(url)
    yield client
    client.close()
    stop_bridge(proc, log)


@pytest.fixture
def uid(request) -> str:
    """A msg_id that is fixed per test but unique across --count repeats."""
    return f"{request.node.originalname}-{next(_ids)}"
