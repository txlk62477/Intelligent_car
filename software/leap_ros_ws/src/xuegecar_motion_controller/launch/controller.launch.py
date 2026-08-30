from launch import LaunchDescription
from launch.substitutions import PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    config = PathJoinSubstitution(
        [FindPackageShare("xuegecar_motion_controller"), "config", "controller.yaml"]
    )
    return LaunchDescription(
        [
            Node(
                package="xuegecar_motion_controller",
                executable="motion_controller",
                name="xuegecar_motion_controller",
                output="screen",
                parameters=[config],
            )
        ]
    )
