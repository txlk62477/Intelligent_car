"""Agent 全局配置与图编译检查。"""

import json
from pathlib import Path

from langgraph.pregel import Pregel

from agent.follow_graph import graph as follow_graph
from agent.graph import SUPERVISOR_PROMPT, graph
from agent.motion_graph import graph as motion_graph


def test_graph_is_compiled() -> None:
    assert isinstance(graph, Pregel)
    assert graph.name == "intelligent_car_supervisor"

    input_schema = graph.get_input_jsonschema()
    assert input_schema["required"] == ["messages"]
    assert set(input_schema["properties"]) == {"messages"}

    output_schema = graph.get_output_jsonschema()
    assert output_schema["required"] == ["messages"]
    assert set(output_schema["properties"]) == {"messages"}


def test_motion_workflow_is_available_as_standalone_graph() -> None:
    """移动子图应可在 LangGraph Studio 中被独立加载。"""
    assert isinstance(motion_graph, Pregel)
    assert motion_graph.name == "relative_motion_workflow"

    input_schema = motion_graph.get_input_jsonschema()
    assert input_schema["required"] == ["motion_actions"]
    assert set(input_schema["properties"]) == {"motion_actions"}

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
    }

    output_schema = follow_graph.get_output_jsonschema()
    assert output_schema["required"] == ["follow_result"]
    assert set(output_schema["properties"]) == {"follow_result"}


def test_langgraph_studio_registers_all_standalone_graphs() -> None:
    """langgraph.json 应注册三个可独立调试的图（含跟随子图）。"""
    config_path = Path(__file__).resolve().parents[2] / "langgraph.json"
    config = json.loads(config_path.read_text())

    graphs = config["graphs"]
    assert graphs["car_agent"].endswith("graph.py:graph")
    assert graphs["relative_motion_workflow"].endswith("motion_graph.py:graph")
    assert graphs["follow_workflow"].endswith("follow_graph.py:graph")


def test_supervisor_prompt_defines_routing_and_limits() -> None:
    assert "get_robot_status" in SUPERVISOR_PROMPT
    assert "stop_robot" in SUPERVISOR_PROMPT
    assert "delegate_to_motion_workflow" in SUPERVISOR_PROMPT
    assert "delegate_to_follow_workflow" in SUPERVISOR_PROMPT
    assert "get_perception_detections" in SUPERVISOR_PROMPT
    assert "立即停车" in SUPERVISOR_PROMPT
    assert "0.05～3" in SUPERVISOR_PROMPT or "0.05" in SUPERVISOR_PROMPT
    assert "/odometry/filtered" in SUPERVISOR_PROMPT
    assert "局部相对里程计" in SUPERVISOR_PROMPT
    assert "0.27 m/s" in SUPERVISOR_PROMPT
    assert "0.53 rad/s" in SUPERVISOR_PROMPT
    assert "悬空或打滑" in SUPERVISOR_PROMPT
    assert "recognize_image" in SUPERVISOR_PROMPT
