"""Agent 全局配置与图编译检查。"""

from langgraph.pregel import Pregel

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


def test_supervisor_prompt_defines_routing_and_limits() -> None:
    assert "get_robot_status" in SUPERVISOR_PROMPT
    assert "stop_robot" in SUPERVISOR_PROMPT
    assert "delegate_to_motion_workflow" in SUPERVISOR_PROMPT
    assert "立即停车" in SUPERVISOR_PROMPT
    assert "0.05～3" in SUPERVISOR_PROMPT or "0.05" in SUPERVISOR_PROMPT
    assert "/odometry/filtered" in SUPERVISOR_PROMPT
    assert "局部相对里程计" in SUPERVISOR_PROMPT
    assert "0.27 m/s" in SUPERVISOR_PROMPT
    assert "0.53 rad/s" in SUPERVISOR_PROMPT
    assert "悬空或打滑" in SUPERVISOR_PROMPT
