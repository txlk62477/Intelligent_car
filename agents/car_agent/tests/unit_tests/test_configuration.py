"""Agent 全局配置、提示词约束与图编译检查。"""

import json
from pathlib import Path

from langgraph.pregel import Pregel

from agent.follow_graph import graph as follow_graph
from agent.graph import FLEXIBLE_AGENT_PROMPT, TERMINAL_EXPLANATION_PROMPT, graph
from agent.location_graph import graph as location_graph
from agent.motion_graph import graph as motion_graph
from agent.navigation_graph import graph as navigation_graph


def test_graph_is_compiled() -> None:
    assert isinstance(graph, Pregel)
    assert graph.name == "intelligent_car_supervisor"

    input_schema = graph.get_input_jsonschema()
    assert input_schema["required"] == ["messages"]
    assert set(input_schema["properties"]) == {"messages"}

    output_schema = graph.get_output_jsonschema()
    assert output_schema["required"] == ["messages"]
    assert set(output_schema["properties"]) == {"messages", "task_progress"}


def test_motion_workflow_is_available_as_standalone_graph() -> None:
    """移动子图应可在 LangGraph Studio 中被独立加载。"""
    assert isinstance(motion_graph, Pregel)
    assert motion_graph.name == "relative_motion_workflow"

    input_schema = motion_graph.get_input_jsonschema()
    assert input_schema["required"] == ["motion_actions"]
    assert set(input_schema["properties"]) == {"motion_actions", "motion_plan_id"}

    output_schema = motion_graph.get_output_jsonschema()
    assert output_schema["required"] == ["motion_result"]
    assert set(output_schema["properties"]) == {"motion_result"}


def test_follow_workflow_is_available_as_standalone_graph() -> None:
    """跟随子图应可在 LangGraph Studio 中被独立加载。"""
    assert isinstance(follow_graph, Pregel)
    assert follow_graph.name == "follow_workflow"

    input_schema = follow_graph.get_input_jsonschema()
    assert input_schema["required"] == [
        "follow_target_label",
        "follow_timeout_seconds",
    ]
    assert set(input_schema["properties"]) == {
        "follow_target_label",
        "follow_timeout_seconds",
        "follow_plan_id",
    }

    output_schema = follow_graph.get_output_jsonschema()
    assert output_schema["required"] == ["follow_result"]
    assert set(output_schema["properties"]) == {"follow_result"}


def test_location_workflow_is_available_as_standalone_graph() -> None:
    """地点教学子图应可在 LangGraph Studio 中被独立加载。"""
    assert isinstance(location_graph, Pregel)
    assert location_graph.name == "map_location_workflow"

    input_schema = location_graph.get_input_jsonschema()
    assert input_schema["required"] == [
        "location_action",
        "location_label",
        "location_aliases",
    ]
    assert set(input_schema["properties"]) == {
        "location_action",
        "location_label",
        "location_aliases",
    }

    output_schema = location_graph.get_output_jsonschema()
    assert output_schema["required"] == ["location_result"]
    assert set(output_schema["properties"]) == {"location_result"}


def test_navigation_workflow_is_available_as_standalone_graph() -> None:
    """地点导航子图应可在 LangGraph Studio 中被独立加载。"""
    assert isinstance(navigation_graph, Pregel)
    assert navigation_graph.name == "map_navigation_workflow"

    input_schema = navigation_graph.get_input_jsonschema()
    assert input_schema["required"] == [
        "location_query",
        "navigation_timeout_seconds",
    ]
    assert set(input_schema["properties"]) == {
        "location_query",
        "navigation_timeout_seconds",
        "navigation_plan_id",
    }

    output_schema = navigation_graph.get_output_jsonschema()
    assert output_schema["required"] == ["navigation_result"]
    assert set(output_schema["properties"]) == {"navigation_result"}


def test_langgraph_studio_registers_all_standalone_graphs() -> None:
    """langgraph.json 应注册全部可独立调试的图（含四个 Workflow）。"""
    config_path = Path(__file__).resolve().parents[2] / "langgraph.json"
    config = json.loads(config_path.read_text())

    graphs = config["graphs"]
    assert graphs["car_agent"].endswith("graph.py:graph")
    assert graphs["relative_motion_workflow"].endswith("motion_graph.py:graph")
    assert graphs["follow_workflow"].endswith("follow_graph.py:graph")
    assert graphs["map_location_workflow"].endswith("location_graph.py:graph")
    assert graphs["map_navigation_workflow"].endswith("navigation_graph.py:graph")


def test_flexible_agent_prompt_defines_boundaries_and_limits() -> None:
    assert "request_workflow" in FLEXIBLE_AGENT_PROMPT
    assert "stop_robot" in FLEXIBLE_AGENT_PROMPT
    assert "ask_user" in FLEXIBLE_AGENT_PROMPT
    assert "kind=motion" in FLEXIBLE_AGENT_PROMPT
    assert "kind=follow" in FLEXIBLE_AGENT_PROMPT
    assert "save_location" in FLEXIBLE_AGENT_PROMPT
    assert "delete_location" in FLEXIBLE_AGENT_PROMPT
    assert "navigation" in FLEXIBLE_AGENT_PROMPT
    # 数值范围与“不得换算/截断/猜测”的硬约束必须留在提示里。
    assert "0.05~3" in FLEXIBLE_AGENT_PROMPT
    assert "1~180" in FLEXIBLE_AGENT_PROMPT
    assert "不得换算、截断、拆小或猜测缺失数值" in FLEXIBLE_AGENT_PROMPT
    assert "一次只能调用一个工具" in FLEXIBLE_AGENT_PROMPT
    assert "观察引用不是执行授权" in FLEXIBLE_AGENT_PROMPT
    assert "局部相对里程计" in FLEXIBLE_AGENT_PROMPT


def test_terminal_explanation_prompt_forbids_tools() -> None:
    assert "不可调用任何工具" in TERMINAL_EXPLANATION_PROMPT
    assert "operation_id" in TERMINAL_EXPLANATION_PROMPT
