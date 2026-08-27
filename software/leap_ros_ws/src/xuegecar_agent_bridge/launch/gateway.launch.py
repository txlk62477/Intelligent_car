from launch import LaunchDescription
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare
from launch.substitutions import PathJoinSubstitution


def generate_launch_description():
    config = PathJoinSubstitution(
        [FindPackageShare("xuegecar_agent_bridge"), "config", "gateway.yaml"]
    )
    return LaunchDescription(
        [
            Node(
                package="xuegecar_agent_bridge",
                executable="gateway",
                name="xuegecar_agent_bridge",
                output="screen",
                parameters=[config],
            )
        ]
    )
