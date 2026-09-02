from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import AndSubstitution, LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    package_share = Path(get_package_share_directory("xuegecar_sensor_fusion"))

    gate_config = LaunchConfiguration("gate_config")
    ekf_config = LaunchConfiguration("ekf_config")
    use_sim_time = ParameterValue(
        LaunchConfiguration("use_sim_time"), value_type=bool
    )

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
            DeclareLaunchArgument(
                "use_sim_time",
                default_value="true",
                description="Use the monotonic /clock published by this launch",
            ),
            DeclareLaunchArgument(
                "start_clock",
                default_value="true",
                description="Start the live monotonic /clock publisher",
            ),
            Node(
                package="xuegecar_sensor_fusion",
                executable="monotonic_clock_node",
                name="monotonic_clock_node",
                output="screen",
                parameters=[{"frequency": 100.0}],
                condition=IfCondition(
                    AndSubstitution(
                        LaunchConfiguration("use_sim_time"),
                        LaunchConfiguration("start_clock"),
                    )
                ),
            ),
            Node(
                package="xuegecar_sensor_fusion",
                executable="sensor_gate_node",
                name="sensor_gate_node",
                output="screen",
                parameters=[gate_config, {"use_sim_time": use_sim_time}],
            ),
            Node(
                package="xuegecar_sensor_fusion",
                executable="scan_restamp_node",
                name="scan_restamp",
                output="screen",
                parameters=[
                    {
                        "scan_in": "/scan",
                        "scan_out": "/scan_ts",
                        "use_sim_time": use_sim_time,
                    }
                ],
            ),
            Node(
                package="robot_localization",
                executable="ekf_node",
                name="ekf_filter_node",
                output="screen",
                parameters=[ekf_config, {"use_sim_time": use_sim_time}],
                remappings=[("odometry/filtered", "/odometry/filtered")],
            ),
        ]
    )
