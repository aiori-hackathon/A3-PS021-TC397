"""U: RESULTS.md generation from an evidence folder (scripts/build_results.py)."""

import json

from scripts import build_results


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def make_run(tmp_path):
    run = tmp_path / "2026-11-02_10-00-00"
    tests = [
        {
            "nodeid": "tests/test_s1_lost_reply.py::test_s1",
            "test_id": "s1",
            "status": "PASS",
            "duration_s": 1,
            "markers": ["official"],
        },
        {
            "nodeid": "tests/test_rules.py::test_r2_a",
            "test_id": "r2",
            "status": "PASS",
            "duration_s": 1,
            "markers": [],
        },
        {
            "nodeid": "tests/test_rules.py::test_r2_b",
            "test_id": "r2",
            "status": "FAIL",
            "duration_s": 1,
            "markers": [],
        },
        {
            "nodeid": "tests/unit/test_codecs.py::test_x[mcp_style-a]",
            "test_id": "x",
            "status": "PASS",
            "duration_s": 0,
            "markers": [],
        },
        {
            "nodeid": "tests/test_s1_lost_reply.py::test_s1[1-2]",
            "test_id": "s1-1",
            "status": "PASS",
            "duration_s": 1,
            "markers": ["official"],
        },
        {
            "nodeid": "tests/test_s1_lost_reply.py::test_s1[2-2]",
            "test_id": "s1-2",
            "status": "TIMEOUT",
            "duration_s": 1,
            "markers": ["official"],
        },
    ]
    write(
        run / "sessions" / "a.json",
        {
            "git_commit": "abc123",
            "python": "3.11.9",
            "os": "Darwin",
            "test_mode": "fast",
            "tests": tests,
        },
    )
    write(
        run / "s1" / "summary.json",
        {
            "test_id": "s1",
            "status": "PASS",
            "numbers": {
                "task_id_preserved": True,
                "side_effects": 1,
                "dialogs_for_msg": 1,
                "duplicates_rejected": {"requests": 1, "effects": 0},
            },
        },
    )
    return run


def test_render_uses_only_recorded_numbers(tmp_path):
    text = build_results.render(build_results.Run(make_run(tmp_path)))
    assert "Run ID: `2026-11-02_10-00-00`" in text and "`abc123`" in text
    assert (
        "| S1 | Lost reply, sender retries | PASS | yes | 1 | requests: 1, effects: 0 | — |" in text
    )
    assert "| S2 | Two workers race | NOT RUN |" in text  # missing evidence is never guessed
    assert "| R2 | Unknown schema version | 400 | FAIL |" in text  # one failing case fails the row
    assert "1/1 codec checks" in text
    assert "--count=2`: 1/2." in text
    assert "| S4 | NOT RUN | NOT RUN |" in text


def test_main_writes_the_file(tmp_path, monkeypatch):
    out = tmp_path / "RESULTS.md"
    monkeypatch.setattr(build_results, "OUT", out)
    monkeypatch.setattr(build_results, "ROOT", tmp_path)
    assert build_results.main(["x", str(make_run(tmp_path))]) == 0
    assert out.read_text().startswith("# Results")
    assert build_results.main(["x", str(tmp_path / "missing")]) == 1
