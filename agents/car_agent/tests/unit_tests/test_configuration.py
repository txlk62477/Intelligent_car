"""Agent 全局配置与图编译检查。"""

from langgraph.pregel import Pregel

from agent.graph import SUPERVISOR_PROMPT, graph


def test_graph_is_compiled() -> None:
    assert isinstance(graph, Pregel)
    assert graph.name == "intelligent_car_supervisor"


def test_supervisor_prompt_defines_routing_and_limits() -> None:
    assert "get_robot_status" in SUPERVISOR_PROMPT
    assert "stop_robot" in SUPERVISOR_PROMPT
    assert "delegate_to_motion_workflow" in SUPERVISOR_PROMPT
    assert "立即停车" in SUPERVISOR_PROMPT
    assert "0.05～3" in SUPERVISOR_PROMPT or "0.05" in SUPERVISOR_PROMPT
