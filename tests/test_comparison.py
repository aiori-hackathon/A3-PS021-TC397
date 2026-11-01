"""M2: protection on vs off over the C1 workload (docs/TESTING.md#m2--protection-on-vs-off-seeded-campaign).

For each seed the same 50-message campaign runs twice, each time in a freshly reset
world: once with protection on, once with DISABLE_DEDUP=1. The seed fixes which
faults and crash points are armed and in what order, so both settings face the same
campaign. Protection on must show no duplicate side effects in any run; protection off
must show some, or the campaign could not tell the two apart.

    pytest -m compare                  # COMPARE_RUNS seeds (default 5), from CHAOS_SEED up
"""

import os
import random

import pytest

from tests.helpers.campaign import run_workload, score, wait_terminal

RUNS = int(os.environ.get("COMPARE_RUNS", "5"))
N_MESSAGES = 50
COUNTED = (
    "messages",
    "tasks",
    "fault_rules_set",
    "crashes",
    "duplicate_side_effects",
    "duplicate_payments",
    "money_paid_twice",
    "payments_without_amount",
    "keys_performed_twice",
    "extra_dialogs",
    "stuck_tasks",
    "failed",
)

pytestmark = [pytest.mark.compare, pytest.mark.timeout(60 + RUNS * 2 * 240)]


def run_once(world, seed: int, protection: bool) -> dict:
    world.supervisor.reset(
        env={
            "AUTO_RESTART": "1",
            "AUTO_CONFIRM": "1",
            "DISABLE_DEDUP": "0" if protection else "1",
        }
    )
    setting = "on" if protection else "off"
    world.caption(f"M2 seed {seed}: protection {setting.upper()}")
    armed = run_workload(world, random.Random(seed), N_MESSAGES, tag=f"M2 {seed} {setting}")
    # With protection off a task may never settle (that is a finding, not a test error).
    dialogs, tasks, settled = wait_terminal(world, N_MESSAGES, timeout=300 if protection else 45)
    status = world.supervisor.status()
    metrics = world.bridge.get("/metrics")
    return {
        "seed": seed,
        "protection": setting,
        "settled": settled,
        **score(dialogs, tasks, world.actions.ledger()),
        "fault_rules_set": sum(armed["faults_set"].values()),
        "crashes": sum(v["crashes"] for v in status.values()),
        "duplicate_requests_absorbed": metrics["duplicate_requests"],
        "duplicate_effects_rejected": metrics["duplicate_effects_rejected"],
    }


def totals(runs: list[dict]) -> dict:
    return {k: sum(r[k] for r in runs) for k in COUNTED} | {"runs": len(runs)}


def test_m2_protection_comparison(world, settings):
    seeds = [settings.chaos_seed + k for k in range(RUNS)]
    runs = []
    for seed in seeds:
        runs.append(run_once(world, seed, protection=True))
        runs.append(run_once(world, seed, protection=False))
    on = [r for r in runs if r["protection"] == "on"]
    off = [r for r in runs if r["protection"] == "off"]
    world.record(
        seeds=seeds,
        messages_per_run=N_MESSAGES,
        runs=runs,
        on=totals(on),
        off=totals(off),
        key_number=(
            f"duplicate side effects over {len(seeds)} seeds: "
            f"{totals(on)['duplicate_side_effects']} with protection on, "
            f"{totals(off)['duplicate_side_effects']} with protection off"
        ),
    )
    for r in on:
        assert r["settled"], f"seed {r['seed']}: protection on left tasks unfinished"
        assert r["duplicate_side_effects"] == 0, f"seed {r['seed']}: {r}"
        assert r["extra_dialogs"] == 0 and r["stuck_tasks"] == 0, f"seed {r['seed']}: {r}"
    off_total = totals(off)
    assert off_total["duplicate_side_effects"] + off_total["extra_dialogs"] > 0, (
        "protection off showed no duplicates: the campaign cannot tell on from off"
    )
