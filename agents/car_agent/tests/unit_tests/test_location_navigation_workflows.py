from __future__ import annotations

from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

from agent.common.robot_gateway import RobotGatewayError
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


async def _seed_location(
    locations: LocationStore, label: str, aliases: list[str], x: float = 1.0
):
    return await locations.save(
        label=label,
        aliases=aliases,
        pose=MapPose(x=x, y=2.0, yaw=0.5),
        map_name="test-map",
        user_id="user-1",
        thread_id="seed",
        run_id="seed",
    )


@pytest.mark.parametrize("action", ["delete", "save"])
@pytest.mark.parametrize("change", ["modified", "deleted", "alias_reassigned"])
async def test_confirmed_location_change_is_bound_to_exact_object(
    action: str, change: str
) -> None:
    store = InMemoryStore()
    gateway = FakeRobotGateway()
    locations = LocationStore(store, robot_id="robot-1", map_id="sha256:test-map")
    await _seed_location(locations, "位置 A", ["桌边"])
    app = build_location_workflow(
        gateway_factory=lambda: gateway, checkpointer=MemorySaver(), store=store
    )
    config = _config(f"bound-{action}-{change}")
    first = await app.ainvoke(
        {
            "location_action": action,
            "location_label": "桌边",
            "location_aliases": ["桌边"],
        },
        config,
    )
    assert first["__interrupt__"][0].value["existing"]["label"] == "位置 A"

    if change == "modified":
        await _seed_location(locations, "位置 A", ["桌边"], x=5.0)
    else:
        await locations.delete("位置 A")
        if change == "alias_reassigned":
            await _seed_location(locations, "位置 B", ["桌边"], x=6.0)
    before = [item.model_dump() for item in await locations.list_all()]
    result = await app.ainvoke(Command(resume=True), config)
    assert result["location_result"]["status"] == "failed"
    assert "原确认已作废" in result["location_result"]["summary"]
    assert [item.model_dump() for item in await locations.list_all()] == before


async def test_new_location_confirmation_invalidated_when_name_becomes_taken() -> None:
    store, gateway = InMemoryStore(), FakeRobotGateway()
    locations = LocationStore(store, robot_id="robot-1", map_id="sha256:test-map")
    app = build_location_workflow(
        gateway_factory=lambda: gateway, checkpointer=MemorySaver(), store=store
    )
    config = _config("name-taken")
    await app.ainvoke(
        {"location_action": "save", "location_label": "门口", "location_aliases": []},
        config,
    )
    inserted = await _seed_location(locations, "门口", [], x=9.0)
    result = await app.ainvoke(Command(resume=True), config)
    assert result["location_result"]["status"] == "failed"
    assert await locations.get("门口") == inserted


async def test_delete_by_alias_removes_only_confirmed_canonical_location() -> None:
    store, gateway = InMemoryStore(), FakeRobotGateway()
    locations = LocationStore(store, robot_id="robot-1", map_id="sha256:test-map")
    await _seed_location(locations, "位置 A", ["桌边"])
    other = await _seed_location(locations, "位置 B", [], x=8.0)
    app = build_location_workflow(
        gateway_factory=lambda: gateway, checkpointer=MemorySaver(), store=store
    )
    config = _config("delete-bound-success")
    await app.ainvoke(
        {"location_action": "delete", "location_label": "桌边", "location_aliases": []},
        config,
    )
    result = await app.ainvoke(Command(resume=True), config)
    assert result["location_result"]["status"] == "success"
    assert await locations.get("位置 A") is None
    assert await locations.get("位置 B") == other


async def test_save_by_alias_updates_confirmed_canonical_location() -> None:
    store, gateway = InMemoryStore(), FakeRobotGateway()
    locations = LocationStore(store, robot_id="robot-1", map_id="sha256:test-map")
    await _seed_location(locations, "位置 A", ["桌边"], x=8.0)
    app = build_location_workflow(
        gateway_factory=lambda: gateway, checkpointer=MemorySaver(), store=store
    )
    config = _config("save-bound-success")
    await app.ainvoke(
        {
            "location_action": "save",
            "location_label": "桌边",
            "location_aliases": ["桌边"],
        },
        config,
    )
    result = await app.ainvoke(Command(resume=True), config)
    assert result["location_result"]["status"] == "success"
    assert (await locations.get("位置 A")).pose.x == 1.0
    assert await locations.get("桌边") is None


async def test_navigation_map_change_after_confirmation_blocks_submission() -> None:
    """确认后地图变化：不提交 goal，按失败处理。"""
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
    config = _config("navigate-map-change")

    await app.ainvoke(
        {
            "location_query": "书桌前",
            "navigation_timeout_seconds": 30.0,
            "navigation_plan_id": "step-1",
        },
        config=config,
    )
    gateway.navigation_status["map_id"] = "sha256:another-map"

    result = await app.ainvoke(Command(resume={"confirmed": True}), config=config)

    assert result["navigation_result"]["status"] == "failed"
    assert "地图发生变化" in result["navigation_result"]["summary"]
    assert gateway.navigation_submitted == []


async def test_navigation_unknown_state_after_submit_is_reported() -> None:
    """goal 已提交但状态无法确认：报告 execution_unknown 并尽力停车。"""
    store = InMemoryStore()
    gateway = FakeRobotGateway(
        submit_errors=[RobotGatewayError("UNAVAILABLE", "响应丢失")],
        query_errors=[RobotGatewayError("UNAVAILABLE", "响应丢失")],
    )
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
    config = _config("navigate-unknown")

    await app.ainvoke(
        {
            "location_query": "书桌前",
            "navigation_timeout_seconds": 30.0,
            "navigation_plan_id": "step-1",
        },
        config=config,
    )
    result = await app.ainvoke(Command(resume={"confirmed": True}), config=config)

    assert result["navigation_result"]["status"] == "execution_unknown"
    assert "状态未知" in result["navigation_result"]["summary"]
    assert gateway.stop_calls == 1
    assert gateway.navigation_submitted == []  # 没有换新 ID 重提


async def test_navigation_lost_response_recovers_same_operation_id() -> None:
    store = InMemoryStore()
    gateway = FakeRobotGateway(
        submit_errors=[RobotGatewayError("UNAVAILABLE", "响应丢失")],
        preloaded_navigations={
            "nav-step-1": {"operation_id": "nav-step-1", "status": "RUNNING"}
        },
        poll_scripts={"nav-step-1": [{"status": "SUCCEEDED"}]},
    )
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
    config = _config("navigate-recovered")

    await app.ainvoke(
        {
            "location_query": "书桌前",
            "navigation_timeout_seconds": 30.0,
            "navigation_plan_id": "step-1",
        },
        config=config,
    )
    result = await app.ainvoke(Command(resume={"confirmed": True}), config=config)

    assert result["navigation_result"]["status"] == "success"
    assert gateway.navigation_submitted == []


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
