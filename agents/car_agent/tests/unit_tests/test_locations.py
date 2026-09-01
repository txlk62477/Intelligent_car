from __future__ import annotations

import pytest
from langgraph.store.memory import InMemoryStore

from agent.memory.locations import LocationStore, MapPose

pytestmark = pytest.mark.anyio


async def _save(locations: LocationStore, label: str = "书桌前"):
    return await locations.save(
        label=label,
        aliases=["桌边"],
        pose=MapPose(x=1.25, y=-0.5, yaw=0.3),
        map_name="room",
        user_id="user-1",
        thread_id="thread-1",
        run_id="run-1",
    )


async def test_locations_are_hard_isolated_by_robot_and_map():
    store = InMemoryStore()
    current = LocationStore(store, robot_id="robot-1", map_id="sha256:map-a")
    await _save(current)

    assert [item.label for item in await current.resolve("桌边")] == ["书桌前"]
    assert (
        await LocationStore(store, robot_id="robot-1", map_id="sha256:map-b").resolve(
            "书桌前"
        )
        == []
    )
    assert (
        await LocationStore(store, robot_id="robot-2", map_id="sha256:map-a").resolve(
            "书桌前"
        )
        == []
    )


async def test_usage_statistics_never_adjust_pose():
    store = InMemoryStore()
    locations = LocationStore(store, robot_id="robot-1", map_id="sha256:map-a")
    original = await _save(locations)

    updated = await locations.record_result(original, "FAILED")

    assert updated.pose == original.pose
    assert updated.failure_count == 1
    assert updated.success_count == 0


async def test_delete_accepts_an_exact_alias_in_current_map():
    store = InMemoryStore()
    locations = LocationStore(store, robot_id="robot-1", map_id="sha256:map-a")
    await _save(locations)

    assert await locations.delete("桌边")
    assert await locations.list_all() == []
