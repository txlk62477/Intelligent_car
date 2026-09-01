"""Compatibility entry point for the unified navigation launch file."""

from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration


def generate_launch_description():
    package_share = Path(get_package_share_directory('xuegecar_navigation2'))

    arguments = {
        'map': LaunchConfiguration('map'),
        'params_file': LaunchConfiguration('params_file'),
        'use_sim_time': LaunchConfiguration('use_sim_time'),
        'autostart': LaunchConfiguration('autostart'),
        'rviz': 'true',
    }

    return LaunchDescription(
        [
            DeclareLaunchArgument(
                'map', default_value=str(package_share / 'maps' / 'room.yaml')
            ),
            DeclareLaunchArgument(
                'params_file',
                default_value=str(package_share / 'param' / 'xuegebot.yaml'),
            ),
            DeclareLaunchArgument('use_sim_time', default_value='true'),
            DeclareLaunchArgument('autostart', default_value='true'),
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(
                    str(package_share / 'launch' / 'navigation2.launch.py')
                ),
                launch_arguments=arguments.items(),
            ),
        ]
    )
