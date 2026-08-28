from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    package_share = Path(get_package_share_directory("xuegecar_sensor_fusion"))

    gate_config = LaunchConfiguration("gate_config")
    ekf_config = LaunchConfiguration("ekf_config")

    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "gate_config",
                default_value=str(package_share / "config" / "sensor_gate.yaml"),
                description="Sensor hard-gate parameter file",
            ),
            DeclareLaunchArgument(
                "ekf_config",
                default_value=str(package_share / "config" / "ekf.yaml"),
                description="robot_localization EKF parameter file",
            ),
            Node(
                package="xuegecar_sensor_fusion",
                executable="sensor_gate_node",
                name="sensor_gate_node",
                output="screen",
                parameters=[gate_config],
            ),
            Node(
                package="robot_localization",
                executable="ekf_node",
                name="ekf_filter_node",
                output="screen",
                parameters=[ekf_config],
                remappings=[("odometry/filtered", "/odometry/filtered")],
            ),
        ]
    )
