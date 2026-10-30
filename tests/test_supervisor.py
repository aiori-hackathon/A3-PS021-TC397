"""Supervisor control API (docs/SUPERVISOR.md), driven through the world fixture."""

import httpx
import pytest

from tests.helpers.wait import wait_until

DEFAULT = ["actions", "bridge", "agent_b", "agent_a"]


def test_reset_starts_the_default_processes(world):
    status = world.supervisor.status()
    assert {name: status[name]["status"] for name in DEFAULT} == dict.fromkeys(DEFAULT, "running")
    assert status["agent_b2"]["status"] == "stopped"
    assert world.bridge.health()["ok"] is True
    assert world.bridge.get("/dialogs") == []
    agents = {a["agent_id"]: a for a in world.bridge.get("/agents")}
    assert agents["agent_a"]["protocol"] == "mcp-style"
    assert agents["agent_b"]["protocol"] == "a2a-style"


def test_kill_shows_crashed_and_restart_recovers(world):
    before = world.supervisor.status()["agent_b"]
    world.supervisor.kill("agent_b")
    after = world.supervisor.status()["agent_b"]
    assert after["status"] == "crashed"
    assert after["exit_code"] == -9  # SIGKILL: no cleanup
    world.supervisor.restart("agent_b")
    again = world.supervisor.status()["agent_b"]
    assert again["status"] == "running" and again["pid"] != before["pid"]
    assert world.agent_b.state()["agent_id"] == "agent_b"


def test_crash_point_is_cleared_on_the_next_restart(world):
    world.supervisor.restart("agent_b", env={"CRASH_AT": "before_pay"})
    assert world.supervisor.status()["agent_b"]["env"] == {"CRASH_AT": "before_pay"}
    world.supervisor.restart("agent_b")  # CRASH_MODE=once (default)
    assert "CRASH_AT" not in world.supervisor.status()["agent_b"]["env"]
    world.supervisor.restart("agent_b", env={"CRASH_AT": "before_pay", "CRASH_MODE": "always"})
    world.supervisor.restart("agent_b")
    assert world.supervisor.status()["agent_b"]["env"]["CRASH_AT"] == "before_pay"


def test_stop_and_start(world):
    world.supervisor.stop("agent_a")
    assert world.supervisor.state("agent_a") == "stopped"
    world.supervisor.start("agent_a")
    assert world.supervisor.state("agent_a") == "running"
    assert world.agent_a.state()["agent_id"] == "agent_a"


def test_reset_wipes_the_world(world):
    plan = world.agent_a.message("pay ₹40 to the bakery", msg_id="wipe")
    wait_until(lambda: world.bridge.task(plan["task_ids"][0])["state"] == "COMPLETED", timeout=10)
    assert world.actions.count() == 1
    world.supervisor.reset()
    assert world.bridge.get("/dialogs") == []
    assert world.actions.count() == 0
    assert world.bridge.events(type="bridge_started")[0]["seq"] == 1


def test_logs_and_unknown_process(world):
    assert any("agent_b" in line for line in world.supervisor.logs("agent_b"))
    r = world.supervisor.http.post("/kill/nobody")
    assert r.status_code == 404


def test_cors_lets_the_dashboard_call_the_supervisor(world):
    r = httpx.options(
        f"{world.supervisor.base_url}/status",
        headers={"Origin": "http://localhost:8000", "Access-Control-Request-Method": "GET"},
    )
    assert r.headers.get("access-control-allow-origin") == "http://localhost:8000"


@pytest.mark.world_env(AUTO_RESTART="1", AUTO_RESTART_DELAY="0.2")
def test_restart_is_not_a_crash_but_a_kill_is_auto_restarted(world):
    # Regression: the supervisor's own kill inside /restart used to count as a
    # crash and schedule a stray auto-restart (X4/X6 flaked 10/40 because of it).
    world.supervisor.restart("agent_b")
    assert world.supervisor.status()["agent_b"]["crashes"] == 0
    pid = world.supervisor.status()["agent_b"]["pid"]
    world.supervisor.kill("agent_b")  # a simulated crash
    wait_until(
        lambda: (
            (s := world.supervisor.status()["agent_b"])["status"] == "running"
            and s["pid"] != pid
            and s["crashes"] == 1
        ),
        timeout=10,
    )
