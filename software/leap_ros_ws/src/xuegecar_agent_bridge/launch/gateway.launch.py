from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch.substitutions import PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    config = PathJoinSubstitution(
        [FindPackageShare("xuegecar_agent_bridge"), "config", "gateway.yaml"]
    )
    perception_config = PathJoinSubstitution(
        [FindPackageShare("xuegecar_perception"), "config", "manager.yaml"]
    )
    control_core = PathJoinSubstitution(
        [FindPackageShare("xuegecar_bringup"), "launch", "control_core.launch.py"]
    )
    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "launch_control_core",
                default_value="true",
                description="Start the shared motion controller and velocity safety pipeline",
            ),
            DeclareLaunchArgument(
                "use_collision_monitor",
                default_value="true",
                description="Enable collision filtering in the shared control core",
            ),
            DeclareLaunchArgument(
                "use_sim_time",
                default_value="true",
                description="Use the monotonic /clock from sensor fusion",
            ),
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(control_core),
                launch_arguments={
                    "use_collision_monitor": LaunchConfiguration(
                        "use_collision_monitor"
                    ),
                    "use_sim_time": LaunchConfiguration("use_sim_time"),
                }.items(),
                condition=IfCondition(LaunchConfiguration("launch_control_core")),
            ),
            Node(
                package="xuegecar_perception",
                executable="perception_manager",
                name="perception_manager",
                output="screen",
                parameters=[perception_config],
            ),
            Node(
                package="xuegecar_agent_bridge",
                executable="gateway",
                name="xuegecar_agent_bridge",
                output="screen",
                parameters=[config],
            ),
        ]
    )
