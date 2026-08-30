from launch import LaunchDescription
from launch.substitutions import PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    config = PathJoinSubstitution(
        [FindPackageShare("xuegecar_agent_bridge"), "config", "gateway.yaml"]
    )
    controller_config = PathJoinSubstitution(
        [FindPackageShare("xuegecar_motion_controller"), "config", "controller.yaml"]
    )
    perception_config = PathJoinSubstitution(
        [FindPackageShare("xuegecar_perception"), "config", "manager.yaml"]
    )
    return LaunchDescription(
        [
            Node(
                package="xuegecar_perception",
                executable="perception_manager",
                name="perception_manager",
                output="screen",
                parameters=[perception_config],
            ),
            Node(
                package="xuegecar_motion_controller",
                executable="motion_controller",
                name="xuegecar_motion_controller",
                output="screen",
                parameters=[controller_config],
            ),
            Node(
                package="xuegecar_agent_bridge",
                executable="gateway",
                name="xuegecar_agent_bridge",
                output="screen",
                parameters=[config],
            )
        ]
    )
