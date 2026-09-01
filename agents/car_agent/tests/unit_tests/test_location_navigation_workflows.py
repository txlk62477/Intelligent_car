from __future__ import annotations

from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

from agent.memory.locations import LocationStore, MapPose
from agent.workflows.location import build_location_workflow
from agent.workflows.navigation import build_navigation_workflow
from unit_tests.fakes import FakeRobotGateway

pytestmark = pytest.mark.anyio


def _config(thread_id: str) -> dict[str, Any]:
    return {
        "configurable": {
            "thread_id": thread_id,
            "user_id": "user-1",
            "robot_id": "robot-1",
        }
    }


async def test_location_save_interrupts_then_rechecks_and_writes_current_map():
    store = InMemoryStore()
    gateway = FakeRobotGateway()
    app = build_location_workflow(
        gateway_factory=lambda: gateway,
        checkpointer=MemorySaver(),
        store=store,
    )
    config = _config("teach-1")

    interrupted = await app.ainvoke(
        {
            "location_action": "save",
            "location_label": "书桌前",
            "location_aliases": ["桌边"],
        },
        config=config,
    )
    assert interrupted["__interrupt__"][0].value["type"] == (
        "confirm_map_location_change"
    )

    result = await app.ainvoke(Command(resume={"confirmed": True}), config=config)

    assert result["location_result"]["status"] == "success"
    locations = LocationStore(store, robot_id="robot-1", map_id="sha256:test-map")
    saved = await locations.resolve("桌边")
    assert saved[0].pose == MapPose(x=1.0, y=2.0, yaw=0.5)


async def test_location_confirmation_is_invalidated_when_map_changes():
    store = InMemoryStore()
    gateway = FakeRobotGateway()
    app = build_location_workflow(
        gateway_factory=lambda: gateway,
        checkpointer=MemorySaver(),
        store=store,
    )
    config = _config("teach-map-change")
    await app.ainvoke(
        {
            "location_action": "save",
            "location_label": "门口",
            "location_aliases": [],
        },
        config=config,
    )
    gateway.navigation_status["map_id"] = "sha256:another-map"

    result = await app.ainvoke(Command(resume={"confirmed": True}), config=config)

    assert result["location_result"]["status"] == "failed"
    assert "地图发生变化" in result["location_result"]["summary"]


async def test_navigation_resolves_only_current_map_and_requires_confirmation(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("NAVIGATION_POLL_INTERVAL", "0.001")
    store = InMemoryStore()
    gateway = FakeRobotGateway()
    locations = LocationStore(store, robot_id="robot-1", map_id="sha256:test-map")
    await locations.save(
        label="书桌前",
        aliases=[],
        pose=MapPose(x=1.0, y=2.0, yaw=0.5),
        map_name="test-map",
        user_id="user-1",
        thread_id="seed",
        run_id="seed",
    )
    app = build_navigation_workflow(
        gateway_factory=lambda: gateway,
        checkpointer=MemorySaver(),
        store=store,
    )
    config = _config("navigate-1")

    interrupted = await app.ainvoke(
        {"location_query": "书桌前", "navigation_timeout_seconds": 30.0},
        config=config,
    )
    assert interrupted["__interrupt__"][0].value["type"] == ("confirm_map_navigation")
    assert gateway.navigation_submitted == []

    result = await app.ainvoke(Command(resume={"confirmed": True}), config=config)

    assert result["navigation_result"]["status"] == "success"
    assert gateway.navigation_submitted[0]["map_id"] == "sha256:test-map"
