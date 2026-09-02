"""Launch-contract tests for the shared XuegeCar control pipeline."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch_ros.actions import Node


WORKSPACE_SRC = Path(__file__).resolve().parents[2]


def _launch_description(package: str, filename: str):
    path = WORKSPACE_SRC / package / 'launch' / filename
    spec = spec_from_file_location(f'{package}.{filename}', path)
    assert spec is not None and spec.loader is not None, path
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.generate_launch_description()


def _nodes(description):
    return [entity for entity in description.entities if isinstance(entity, Node)]


def _includes(description):
    return [
        entity
        for entity in description.entities
        if isinstance(entity, IncludeLaunchDescription)
    ]


def _arguments(description):
    return {
        entity.name: entity
        for entity in description.entities
        if isinstance(entity, DeclareLaunchArgument)
    }


def _text(value) -> str:
    if isinstance(value, str):
        return value
    if hasattr(value, 'text'):
        return value.text
    try:
        return ''.join(
            part.text if hasattr(part, 'text') else str(part) for part in value
        )
    except TypeError:
        return str(value)


def _node_executable(node: Node) -> str:
    return _text(node.node_executable)


def _include_arguments(include: IncludeLaunchDescription) -> dict[str, str]:
    return {
        name: _text(value)
        for name, value in include.launch_arguments
    }


def test_control_core_owns_one_mux_and_optional_collision_monitor():
    description = _launch_description('xuegecar_bringup', 'control_core.launch.py')
    nodes = _nodes(description)
    executables = [_node_executable(node) for node in nodes]

    assert executables.count('twist_mux') == 1
    assert executables.count('collision_monitor') == 1
    assert executables.count('motion_controller') == 1
    assert executables.count('robot_state_publisher') == 1
    assert executables.count('joint_state_publisher') == 1

    collision_node = nodes[executables.index('collision_monitor')]
    assert collision_node.condition is not None

    arguments = _arguments(description)
    assert 'use_collision_monitor' in arguments
    assert _text(arguments['use_collision_monitor'].default_value) == 'true'


def test_gateway_delegates_control_core_instead_of_starting_controller():
    description = _launch_description('xuegecar_agent_bridge', 'gateway.launch.py')

    assert 'launch_control_core' in _arguments(description)
    assert all(_node_executable(node) != 'motion_controller' for node in _nodes(description))
    assert len(_includes(description)) == 1


def test_navigation_delegates_mux_and_collision_monitor_to_control_core():
    description = _launch_description('xuegecar_navigation2', 'navigation2.launch.py')
    executables = [_node_executable(node) for node in _nodes(description)]

    assert 'launch_control_core' in _arguments(description)
    assert 'twist_mux' not in executables
    assert 'collision_monitor' not in executables
    assert len(_includes(description)) == 1


def test_web_gui_delegates_mux_to_control_core():
    description = _launch_description(
        'xuegecar_web_gui', 'xuegecar_web_gui.launch.py'
    )
    executables = [_node_executable(node) for node in _nodes(description)]

    assert 'twist_mux' not in executables
    assert len(_includes(description)) == 2  # control core plus optional camera


def test_full_control_includes_core_once_and_disables_nested_owners():
    description = _launch_description('xuegecar_bringup', 'full_control.launch.py')
    includes = _includes(description)

    assert len(includes) == 4
    include_arguments = [_include_arguments(include) for include in includes]
    assert sum(args.get('launch_control_core') == 'false' for args in include_arguments) == 2
    assert sum(args.get('launch_twist_mux') == 'false' for args in include_arguments) == 1
