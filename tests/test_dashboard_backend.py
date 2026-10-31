"""Routes the dashboard depends on (docs/DASHBOARD.md). The page itself is checked
by hand against docs/DEMO_SCRIPT.md; these tests pin down its data sources."""

import re
from pathlib import Path

import pytest

from tests.helpers.wait import wait_until

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboard"


def test_bridge_serves_the_dashboard_and_its_files(fresh_bridge):
    page = fresh_bridge.http.get("/")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    assert "dialog-state/0.1-experimental" in page.text
    for name in ("app.js", "style.css"):
        assert fresh_bridge.http.get(f"/static/{name}").status_code == 200


def test_dashboard_files_load_nothing_from_the_internet():
    for path in DASHBOARD.glob("*"):
        if path.suffix not in (".html", ".js", ".css"):
            continue
        text = path.read_text(encoding="utf-8")
        external = re.findall(r"""(?:src|href)\s*=\s*["'](https?:)?//""", text) + re.findall(
            r"@import\s+url\(\s*['\"]?https?:", text
        )
        assert not external, f"{path.name} loads something from the internet"
        assert "cdn" not in text.lower(), path.name


def test_config_json_tells_the_page_where_everything_is(fresh_bridge, settings):
    cfg = fresh_bridge.get("/config.json")
    assert cfg["schema"] == "dialog-state/0.1-experimental"
    assert set(cfg["ports"]) == {
        "bridge",
        "agent_a",
        "agent_b",
        "agent_b2",
        "actions",
        "supervisor",
    }
    assert cfg["ports"]["supervisor"] == settings.supervisor_port
    assert cfg["lease_seconds"] == settings.lease_seconds
    assert "protection" in cfg["event_groups"]
    assert "duplicate_request" in cfg["event_groups"]["protection"]


def test_retry_resends_the_last_request_with_the_same_key(world):
    plan = world.agent_a.message("write me a poem", msg_id="rt")
    r = world.agent_a.http.post("/retry")
    assert r.status_code == 200
    body = r.json()
    assert body["request_id"] == "rt:create"  # a general task needs no confirmation
    assert body["ok"] is True
    repeats = [
        e
        for e in world.bridge.events(type="duplicate_request")
        if e["data"]["request_id"] == body["request_id"]
    ]
    assert len(repeats) == 1
    assert len([d for d in world.bridge.get("/dialogs") if d["msg_id"] == "rt"]) == 1
    assert plan["dialog_id"]


def test_retry_with_nothing_sent_yet(world):
    r = world.agent_a.http.post("/retry")
    assert r.status_code == 409


def test_world_reports_its_environment(world):
    w = world.supervisor._get("/world")
    assert w["mode"] == "test" and w["protection"] is True and w["env"] == {}


@pytest.mark.world_env(DISABLE_DEDUP="1")
def test_world_shows_protection_off(world):
    assert world.supervisor._get("/world")["protection"] is False


def test_supervisor_runs_tests_and_reports_progress(supervisor):
    started = supervisor._post("/tests/run", {"mode": "fast", "select": "unit-models"})
    run_id = started["run_id"]
    done = wait_until(
        lambda: (r := supervisor._get(f"/tests/{run_id}"))["state"] == "done" and r, timeout=60
    )
    assert done["exit_code"] == 0
    assert done["tests"] and set(done["tests"].values()) == {"PASS"}
    assert any(r["run_id"] == run_id for r in supervisor._get("/tests"))


def test_supervisor_refuses_unknown_selections_and_watch_in_the_test_world(supervisor):
    r = supervisor.http.post("/tests/run", json={"mode": "fast", "select": "nope"})
    assert r.status_code == 400
    r = supervisor.http.post("/tests/run", json={"mode": "watch", "select": "s4"})
    assert r.status_code == 400  # watch runs against the demo world, from the demo supervisor


def test_supervisor_serves_evidence_files(supervisor):
    import json
    import shutil

    folder = ROOT / "results" / "_dashboard_test" / "s9"
    folder.mkdir(parents=True, exist_ok=True)
    try:
        (folder / "summary.json").write_text(json.dumps({"test_id": "s9", "status": "PASS"}))
        runs = {r["run_id"]: r for r in supervisor._get("/results")}
        assert runs["_dashboard_test"]["tests"] == ["s9"]
        got = supervisor.http.get("/results/_dashboard_test/s9/summary.json")
        assert got.status_code == 200 and got.json()["status"] == "PASS"
        assert supervisor.http.get("/results/_dashboard_test/s9/nope.json").status_code == 404
        assert supervisor.http.get("/results/..%2Fconfig.py/x/y").status_code in (400, 404)
    finally:
        shutil.rmtree(folder.parent)


def test_watch_mode_multiplies_test_timeouts():
    from tests import conftest

    assert conftest.watch_timeout(20, "watch") == 100
    assert conftest.watch_timeout(20, "fast") == 20


def test_dashboard_files_are_never_served_stale(fresh_bridge):
    for path in ("/", "/static/app.js", "/static/style.css"):
        assert fresh_bridge.http.get(path).headers.get("cache-control") == "no-cache", path
    assert fresh_bridge.http.get("/health").headers.get("cache-control") is None
