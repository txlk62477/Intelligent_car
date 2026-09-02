"""Regression tests for the measured robot geometry and safety envelope."""

from ast import literal_eval
from pathlib import Path
from xml.etree import ElementTree

import yaml


WORKSPACE_SRC = Path(__file__).resolve().parents[2]
BODY_FOOTPRINT = [
    [0.124, 0.0725],
    [0.124, -0.0725],
    [-0.0325, -0.0725],
    [-0.0325, 0.0725],
]
STOP_FOOTPRINT = [
    [0.144, 0.0925],
    [0.144, -0.0925],
    [-0.0525, -0.0925],
    [-0.0525, 0.0925],
]


def _xyz(joint: ElementTree.Element) -> list[float]:
    origin = joint.find('origin')
    assert origin is not None
    return [float(value) for value in origin.attrib['xyz'].split()]


def _joint(root: ElementTree.Element, name: str) -> ElementTree.Element:
    joint = root.find(f"./joint[@name='{name}']")
    assert joint is not None
    return joint


def test_urdf_uses_axle_centre_and_measured_lidar_pose():
    urdf = (
        WORKSPACE_SRC
        / 'xuegecar_description'
        / 'urdf'
        / 'xuegecar.urdf'
    )
    root = ElementTree.parse(urdf).getroot()

    assert _xyz(_joint(root, 'base_joint')) == [0.0, 0.0, 0.0325]
    assert _xyz(_joint(root, 'laser_joint')) == [0.020, 0.0, 0.1055]
    assert _xyz(_joint(root, 'left_wheel_joint')) == [0.0, 0.0525, 0.0]
    assert _xyz(_joint(root, 'right_wheel_joint')) == [0.0, -0.0525, 0.0]

    body = root.find("./link[@name='base_link']/collision/geometry/box")
    assert body is not None
    assert [float(value) for value in body.attrib['size'].split()] == [
        0.1565,
        0.145,
        0.145,
    ]


def test_nav2_costmaps_share_measured_body_footprint():
    params = yaml.safe_load(
        (
            WORKSPACE_SRC
            / 'xuegecar_navigation2'
            / 'param'
            / 'xuegebot.yaml'
        ).read_text(encoding='utf-8')
    )

    local = params['local_costmap']['local_costmap']['ros__parameters']
    global_ = params['global_costmap']['global_costmap']['ros__parameters']
    assert literal_eval(local['footprint']) == BODY_FOOTPRINT
    assert literal_eval(global_['footprint']) == BODY_FOOTPRINT


def test_collision_monitor_has_ttc_approach_and_expanded_hard_stop():
    params = yaml.safe_load(
        (
            WORKSPACE_SRC
            / 'xuegecar_bringup'
            / 'config'
            / 'collision_monitor.yaml'
        ).read_text(encoding='utf-8')
    )['collision_monitor']['ros__parameters']

    assert params['polygons'] == ['EmergencyStop', 'FootprintApproach']

    stop = params['EmergencyStop']
    assert stop['action_type'] == 'stop'
    assert literal_eval(stop['points']) == STOP_FOOTPRINT

    approach = params['FootprintApproach']
    assert approach['type'] == 'polygon'
    assert approach['action_type'] == 'approach'
    assert literal_eval(approach['points']) == BODY_FOOTPRINT
    assert approach['time_before_collision'] == 0.3
    assert approach['simulation_time_step'] == 0.05


def test_urdf_is_the_only_sensor_static_tf_authority():
    launch_files = [
        WORKSPACE_SRC / 'xuegecar_sensor_fusion' / 'launch' / 'fusion.launch.py',
        WORKSPACE_SRC / 'camsense_lidar' / 'launch' / 'camsense_lidar.launch.py',
        WORKSPACE_SRC / 'xuegecar_cartographer' / 'launch' / 'cartographer.launch.py',
    ]
    for launch_file in launch_files:
        assert 'static_transform_publisher' not in launch_file.read_text(
            encoding='utf-8'
        )

    ekf = yaml.safe_load(
        (
            WORKSPACE_SRC / 'xuegecar_sensor_fusion' / 'config' / 'ekf.yaml'
        ).read_text(encoding='utf-8')
    )['ekf_filter_node']['ros__parameters']
    assert ekf['base_link_frame'] == 'base_footprint'
