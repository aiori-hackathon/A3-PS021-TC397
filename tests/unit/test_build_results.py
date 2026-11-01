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


def test_recovery_skips_the_resume_transaction_itself():
    ev = [
        {
            "task_id": "t",
            "type": "checkpoint",
            "actor": "agent_b",
            "data": {},
            "ts": "2026-11-02T10:00:00.000Z",
        },
        {
            "task_id": "t",
            "type": "state_changed",
            "actor": "agent_b",
            "data": {},
            "ts": "2026-11-02T10:00:02.500Z",
        },  # same transaction as the resume
        {
            "task_id": "t",
            "type": "resumed",
            "actor": "agent_b",
            "data": {},
            "ts": "2026-11-02T10:00:02.500Z",
        },
    ]
    assert build_results.recovery_ms_from_events(ev) == 2500.0
    assert build_results.recovery_ms_from_events(ev[:1]) is None


def m2_numbers():
    def run(seed, setting, dups, pays, money, dialogs, stuck):
        return {
            "seed": seed,
            "protection": setting,
            "settled": stuck == 0,
            "duplicate_side_effects": dups,
            "duplicate_payments": pays,
            "money_paid_twice": money,
            "extra_dialogs": dialogs,
            "stuck_tasks": stuck,
        }

    on = {"runs": 2, "messages": 100, "tasks": 130, "fault_rules_set": 30, "crashes": 9}
    on |= {"duplicate_side_effects": 0, "duplicate_payments": 0, "money_paid_twice": 0}
    on |= {"keys_performed_twice": 0, "extra_dialogs": 0, "stuck_tasks": 0, "failed": 0}
    off = dict(on, tasks=141, crashes=8, duplicate_side_effects=7, duplicate_payments=5)
    off |= {"money_paid_twice": 1600, "keys_performed_twice": 4, "extra_dialogs": 9}
    off |= {"stuck_tasks": 3, "payments_without_amount": 1}
    return {
        "seeds": [42, 43, 44],
        "messages_per_run": 50,
        "on": on,
        "off": off,
        "runs": [
            run(42, "on", 0, 0, 0, 0, 0),
            run(42, "off", 4, 3, 1000, 6, 3),
            run(43, "on", 0, 0, 0, 0, 0),
            run(43, "off", 3, 2, 600, 3, 0),
            run(44, "on", 0, 0, 0, 0, 0),
            {**run(44, "off", 1, 1, 0, 0, 0), "payments_without_amount": 1},
        ],
    }


def test_comparison_section_shows_both_settings_and_every_seed():
    lines = build_results.comparison_lines(m2_numbers(), "PASS")
    text = "\n".join(lines)
    assert "| **Duplicate side effects** | **0** | **7** |" in text
    assert "| of which payments (amount paid again) | 0 | 5 (₹1,600 + 1 with no amount) |" in text
    assert "| Runs (seeds) | 2 (42–44), 50 messages each | 2 (same seeds and faults) |" in text
    assert "| 42 | 0 | 4 | 3 (₹1,000) | 6 | 3 |" in text
    assert "| 44 | 0 | 1 | 1 (1 with no amount) | 0 | 0 |" in text
    assert "| 43 | 0 | 3 | 2 (₹600) | 3 | 0 |" in text
    assert "Status: PASS" in text


def test_comparison_section_when_not_run():
    text = "\n".join(build_results.comparison_lines({}, build_results.NOT_RUN))
    assert build_results.NOT_RUN in text and "| **Duplicate side effects**" not in text


def test_headline_uses_the_comparison_when_present():
    assert build_results.headline(m2_numbers()) == (
        "Same 2 seeded fault campaigns (100 messages): **0** duplicate side effects with "
        "protection on, **7** with it off (5 of them payments)."
    )
    assert build_results.headline({}) is None
