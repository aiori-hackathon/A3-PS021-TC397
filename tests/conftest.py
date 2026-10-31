"""Fixtures. See docs/TESTING.md.

- `world`: the full system under the supervisor, reset to empty before each
  test. Fast mode uses the test world (ports 9000-9009); watch mode uses the
  demo world (8000-8009) with demo timings and captions.
- `bridge` / `fresh_bridge` / `actions_service`: a single bare process on a
  free port with its own data folder, for bridge-level and service-level
  tests that must not have agents running (for example manual claim_next).
"""

from __future__ import annotations

import itertools
import json
import os
import platform
import re
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

import config
from tests.helpers.clients import (
    ActionsClient,
    AgentAClient,
    AgentBClient,
    BridgeClient,
    SupervisorClient,
)
from tests.helpers.wait import TIMEOUT_FACTOR, wait_until

ROOT = Path(__file__).resolve().parents[1]
TEST_MODE = os.environ.get("TEST_MODE", "fast")
WORLD_MODE = "demo" if TEST_MODE == "watch" else "test"
_ids = itertools.count(1)
RUN_ID = os.environ.get("RUN_ID") or time.strftime("%Y-%m-%d_%H-%M-%S")
RESULTS = ROOT / "results" / RUN_ID
_outcomes: list[dict] = []


# --- evidence: outcomes for every test, folders for world tests ----------------------


def test_id(item) -> str:
    """s1, x4, r2, i3, m1, c1 ... from the test name; anything else keeps its name."""
    m = re.match(r"test_([a-z]\d+(?:_[a-z]\d+)?)_", item.originalname or item.name)
    base = m.group(1) if m else (item.originalname or item.name).removeprefix("test_")
    repeat = re.search(r"\[(\d+)-\d+\]$", item.name)  # pytest-repeat suffix
    return f"{base}-{repeat.group(1)}" if repeat else base


def _status(report) -> str:
    if report.passed:
        return "PASS"
    if report.skipped:
        return "SKIP"
    text = str(report.longrepr)
    # A wait that gave up or the per-test timeout means the system got stuck.
    if "TimeoutError" in text or "Timeout >" in text:
        return "TIMEOUT"
    return "FAIL" if report.when == "call" else "ERROR"


def git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=False
        ).stdout.strip()
        return out.stdout.strip() + ("+dirty" if dirty else "")
    except OSError:
        return "unknown"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    setattr(item, f"rep_{report.when}", report)
    if report.when == "call" or (report.when == "setup" and not report.passed):
        _outcomes.append(
            {
                "nodeid": item.nodeid,
                "test_id": test_id(item),
                "status": _status(report),
                "duration_s": round(report.duration, 3),
                "markers": sorted({m.name for m in item.iter_markers()}),
            }
        )


def pytest_sessionfinish(session, exitstatus):
    if not _outcomes:
        return
    folder = RESULTS / "sessions"
    folder.mkdir(parents=True, exist_ok=True)
    record = {
        "run_id": RUN_ID,
        "started_by": " ".join(sys.argv[1:]),
        "test_mode": TEST_MODE,
        "disable_dedup": os.environ.get("DISABLE_DEDUP", "0"),
        "git_commit": git_commit(),
        "python": platform.python_version(),
        "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tests": _outcomes,
    }
    name = f"{time.strftime('%H-%M-%S')}-{os.getpid()}.json"
    (folder / name).write_text(json.dumps(record, indent=2), encoding="utf-8")


def watch_timeout(seconds: float, mode: str) -> float:
    """Per-test timeout: x5 in watch mode (docs/TESTING.md#timeouts-three-levels)."""
    return seconds * 5 if mode == "watch" else seconds


def pytest_collection_modifyitems(config, items):
    if TEST_MODE != "watch":
        return
    default = float(config.getini("timeout") or 0)
    for item in items:
        mark = item.get_closest_marker("timeout")
        base = float(mark.args[0]) if mark and mark.args else default
        item.add_marker(pytest.mark.timeout(watch_timeout(base, TEST_MODE)))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _healthy(url: str, path: str = "/health") -> bool:
    try:
        return httpx.get(f"{url}{path}", timeout=0.5).status_code == 200
    except httpx.HTTPError:
        return False


# --- bare single processes ------------------------------------------------------


def start_service(app: str, data_dir: Path, log_path: Path, extra_env: dict | None = None):
    port = free_port()
    env = {**os.environ, "MODE": "test", "DATA_DIR": str(data_dir), **(extra_env or {})}
    log = open(log_path, "ab")  # noqa: SIM115 (closed by stop_process)
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", app, "--host", "127.0.0.1", "--port", str(port)],
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
        stop_process(proc, log)
        raise
    if proc.poll() is not None:
        log.close()
        raise RuntimeError(f"{app} exited with {proc.returncode}; see {log_path}")
    return proc, log, url


def stop_process(proc: subprocess.Popen, log=None) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    if log:
        log.close()


def start_bridge(data_dir: Path, log_path: Path, extra_env: dict | None = None):
    return start_service("bridge.main:app", data_dir, log_path, extra_env)


stop_bridge = stop_process


@pytest.fixture(scope="session")
def settings() -> config.Settings:
    return config.load(WORLD_MODE)


@pytest.fixture(scope="session")
def bridge(tmp_path_factory) -> Iterator[BridgeClient]:
    """One bare bridge for the whole session. Tests must use their own IDs."""
    data = tmp_path_factory.mktemp("bridge")
    proc, log, url = start_bridge(data, data / "bridge.log")
    client = BridgeClient(url)
    yield client
    client.close()
    stop_process(proc, log)


@pytest.fixture
def fresh_bridge(tmp_path) -> Iterator[BridgeClient]:
    """A private, empty bare bridge for tests that look at global state."""
    proc, log, url = start_bridge(tmp_path, tmp_path / "bridge.log")
    client = BridgeClient(url)
    yield client
    client.close()
    stop_process(proc, log)


@pytest.fixture
def actions_service(tmp_path) -> Iterator[ActionsClient]:
    proc, log, url = start_service("services.actions:app", tmp_path, tmp_path / "actions.log")
    client = ActionsClient(url)
    yield client
    client.close()
    stop_process(proc, log)


@pytest.fixture
def uid(request) -> str:
    """An ID that is fixed per test but unique across --count repeats."""
    return f"{request.node.originalname}-{next(_ids)}"


# --- the supervised world ----------------------------------------------------------


class World:
    def __init__(self, settings: config.Settings, supervisor: SupervisorClient) -> None:
        self.settings = settings
        self.supervisor = supervisor
        self.bridge = BridgeClient(settings.url(settings.bridge_port), timeout=10)
        self.agent_a = AgentAClient(settings.url(settings.agent_a_port))
        self.agent_b = AgentBClient(settings.url(settings.agent_b_port))
        self.agent_b2 = AgentBClient(settings.url(settings.agent_b2_port))
        self.actions = ActionsClient(settings.url(settings.actions_port))
        self.numbers: dict = {}

    def record(self, **numbers) -> None:
        """Key numbers for RESULTS.md, saved in this test's summary.json."""
        self.numbers.update(numbers)

    def caption(self, text: str) -> None:
        """Watch-mode caption on the dashboard; a no-op in fast mode."""
        if TEST_MODE == "watch":
            self.bridge.http.post("/annotate", json={"text": text})

    def close(self) -> None:
        for client in (self.bridge, self.agent_a, self.agent_b, self.agent_b2, self.actions):
            client.close()


@pytest.fixture(scope="session")
def supervisor(settings) -> Iterator[SupervisorClient]:
    """Use a running supervisor for this world, or start one for the session."""
    url = settings.url(settings.supervisor_port)
    proc = log = None
    if not _healthy(url, "/status"):
        log_dir = settings.log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        log = open(log_dir / "supervisor.log", "ab")  # noqa: SIM115 (closed below)
        proc = subprocess.Popen(
            [sys.executable, "supervisor.py", "--mode", WORLD_MODE, "--no-start"],
            cwd=ROOT,
            env={**os.environ, "MODE": WORLD_MODE},
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        wait_until(lambda: proc.poll() is not None or _healthy(url, "/status"), timeout=15)
        if proc.poll() is not None:
            raise RuntimeError(f"supervisor exited; see {log_dir / 'supervisor.log'}")
    client = SupervisorClient(url, timeout=60 * TIMEOUT_FACTOR)
    yield client
    client.close()
    if proc is not None:
        stop_process(proc, log)


def world_env(request) -> dict[str, str]:
    """Environment for this test's world: mode needs plus @pytest.mark.world_env(...)."""
    env: dict[str, str] = {}
    if TEST_MODE == "watch":
        env["AUTO_CONFIRM"] = "1"  # the demo world defaults to 0
    if os.environ.get("DISABLE_DEDUP"):
        env["DISABLE_DEDUP"] = os.environ["DISABLE_DEDUP"]
    for mark in reversed(list(request.node.iter_markers("world_env"))):
        env.update({k: str(v) for k, v in mark.kwargs.items()})
    return env


def save_evidence(w: World, item, env: dict, started: float) -> None:
    """results/<run_id>/<test_id>/: events, tasks, metrics, ledger, faults, summary."""
    folder = RESULTS / test_id(item)
    folder.mkdir(parents=True, exist_ok=True)
    notes = []
    try:
        w.bridge.http.post("/admin/export", json={"path": str(folder.relative_to(ROOT))})
    except httpx.HTTPError as exc:
        notes.append(f"bridge export failed: {exc!r}")
    try:
        (folder / "ledger.json").write_text(json.dumps(w.actions.ledger(), indent=2))
    except httpx.HTTPError as exc:
        notes.append(f"ledger export failed: {exc!r}")
    faults = {}
    for name, client in (("bridge", w.bridge), ("actions", w.actions)):
        try:
            faults[f"{name}_active"] = client.http.get("/faults").json()
        except httpx.HTTPError:
            faults[f"{name}_active"] = None
    events_file = folder / "events.json"
    if events_file.exists():
        events = json.loads(events_file.read_text())
        faults["applied"] = [e for e in events if e["type"] == "fault_applied"]
        faults["rules_set"] = [e for e in events if e["type"] == "fault_rule_set"]
    (folder / "faults.json").write_text(json.dumps(faults, indent=2))
    call = getattr(item, "rep_call", None)
    try:
        crashes = {n: v["crashes"] for n, v in w.supervisor.status().items()}
    except httpx.HTTPError:
        crashes = None
    summary = {
        "test_id": test_id(item),
        "nodeid": item.nodeid,
        "status": _status(call) if call else "ERROR",
        "duration_s": round(time.monotonic() - started, 2),
        "numbers": w.numbers,
        "crashes": crashes,
        "world_env": env,
        "mode": WORLD_MODE,
        "test_mode": TEST_MODE,
        "seed": os.environ.get("CHAOS_SEED", str(w.settings.chaos_seed)),
        "git_commit": git_commit(),
        "python": platform.python_version(),
        "os": f"{platform.system()} {platform.release()}",
        "notes": notes,
    }
    (folder / "summary.json").write_text(json.dumps(summary, indent=2))


@pytest.fixture
def world(supervisor, settings, request) -> Iterator[World]:
    env = world_env(request)
    supervisor.reset(env=env)
    w = World(settings, supervisor)
    started = time.monotonic()
    yield w
    save_evidence(w, request.node, env, started)  # runs even when the test failed
    w.close()
