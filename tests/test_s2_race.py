"""S2: two workers race for one task (docs/TESTING.md#s2--two-workers-race)."""

import asyncio

import httpx
import pytest

from tests.helpers.scenario import PAY, events_of, task_in


@pytest.mark.official
def test_s2_two_workers_race(world):
    world.caption("Starting a second worker; pausing both claim loops")
    world.supervisor.start("agent_b2")
    world.agent_b.pause()
    world.agent_b2.pause()

    plan = world.agent_a.message(PAY, msg_id="s2")
    [task_id] = plan["task_ids"]
    task_in(world, task_id, "DISPATCHED")

    world.caption("Both workers claim the same task at the same moment")

    async def race():
        async with httpx.AsyncClient(timeout=10) as client:
            return await asyncio.gather(
                client.post(f"{world.agent_b.base_url}/claim", json={"task_id": task_id}),
                client.post(f"{world.agent_b2.base_url}/claim", json={"task_id": task_id}),
            )

    results = asyncio.run(race())
    assert sorted(r.status_code for r in results) == [200, 409]
    winner = next(r for r in results if r.status_code == 200).json()
    loser = next(r for r in results if r.status_code == 409).json()
    assert winner["ok"] is True and loser["error"]["code"] == "already_claimed"
    assert winner["task"]["fence"] == 1
    assert world.bridge.task(task_id)["fence"] == 1

    world.caption("Resuming both workers; the winner finishes the task")
    world.agent_b.unpause()
    world.agent_b2.unpause()
    done = task_in(world, task_id, "COMPLETED")
    assert done["fence"] == 1 and done["owner"] == winner["task"]["owner"]
    assert [e["type"] for e in events_of(world, task_id)].count("claimed") == 1
    assert world.actions.count(task_id=task_id, action="pay") == 1
