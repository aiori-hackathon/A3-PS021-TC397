"""U: the supervisor reads live test progress from pytest -v output."""

import supervisor


def test_plain_and_coloured_result_lines():
    plain = "tests/test_s4_crash_after_effect.py::test_s4_crash_after_side_effect PASSED [ 17%]"
    coloured = "tests/test_rules.py::test_r3_stale_fence \x1b[32mPASSED\x1b[0m\x1b[32m          [ 16%]\x1b[0m"
    assert supervisor.parse_result_line(plain) == ("s4", "PASS")
    assert supervisor.parse_result_line(coloured) == ("r3", "PASS")
    failed = "tests/test_extras.py::test_x4_retry_limit[3-20] FAILED [ 40%]"
    assert supervisor.parse_result_line(failed) == ("x4", "FAIL")
    assert supervisor.parse_result_line("collected 443 items") is None


def test_test_ids_match_the_evidence_folders():
    assert supervisor.test_id_of("test_m1_s1_protection_off") == "m1_s1"
    assert supervisor.test_id_of("test_c1_chaos_run") == "c1"
    assert (
        supervisor.test_id_of("test_retry_with_nothing_sent_yet") == "retry_with_nothing_sent_yet"
    )
