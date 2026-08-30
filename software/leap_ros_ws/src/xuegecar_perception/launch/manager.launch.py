from launch import LaunchDescription
from launch.substitutions import PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    config = PathJoinSubstitution(
        [FindPackageShare("xuegecar_perception"), "config", "manager.yaml"]
    )
    return LaunchDescription(
        [
            Node(
                package="xuegecar_perception",
                executable="perception_manager",
                name="perception_manager",
                output="screen",
                parameters=[config],
            )
        ]
    )
