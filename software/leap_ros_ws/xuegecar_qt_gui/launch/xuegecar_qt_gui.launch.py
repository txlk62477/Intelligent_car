from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription(
        [
            Node(
                package="xuegecar_qt_gui",
                executable="xuegecar_qt_gui",
                name="xuegecar_qt_gui",
                output="screen",
                parameters=[
                    {
                        "camera_topic": "/camera/image_raw/compressed",
                        "scan_topic": "/scan",
                        "map_topic": "/map",
                        "odom_topic": "/odom",
                        "cmd_vel_topic": "/cmd_vel",
                        "goal_topic": "/goal_pose",
                        "fixed_frame": "map",
                        "base_frame": "base_link",
                        "fallback_base_frame": "base_footprint",
                    }
                ],
            )
        ]
    )
