import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    package_share = get_package_share_directory('xuegecar_mavlink_bridge')
    default_config = os.path.join(package_share, 'config', 'mavlink_bridge.yaml')
    config_file = LaunchConfiguration('config_file')

    return LaunchDescription([
        DeclareLaunchArgument(
            'config_file',
            default_value=default_config,
            description='YAML config file for the MAVLink bridge',
        ),
        Node(
            package='xuegecar_mavlink_bridge',
            executable='mavlink_bridge',
            name='mavlink_bridge',
            output='screen',
            parameters=[config_file],
        ),
    ])
