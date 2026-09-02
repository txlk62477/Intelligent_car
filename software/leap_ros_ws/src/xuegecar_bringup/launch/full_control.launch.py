"""Start the complete XuegeCar control stack with one shared velocity mux."""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.substitutions import FindPackageShare


def _launch_file(package: str, filename: str) -> PythonLaunchDescriptionSource:
    return PythonLaunchDescriptionSource(
        PathJoinSubstitution([FindPackageShare(package), 'launch', filename])
    )


def generate_launch_description():
    navigation_share = FindPackageShare('xuegecar_navigation2')

    return LaunchDescription(
        [
            DeclareLaunchArgument(
                'launch_gateway',
                default_value='true',
                description='Start Agent Gateway and Perception Manager',
            ),
            DeclareLaunchArgument(
                'launch_navigation',
                default_value='true',
                description='Start localization and Nav2',
            ),
            DeclareLaunchArgument(
                'launch_web_gui',
                default_value='true',
                description='Start the browser control interface',
            ),
            DeclareLaunchArgument(
                'use_collision_monitor',
                default_value='true',
                description='Filter all selected velocities for imminent collisions',
            ),
            DeclareLaunchArgument(
                'use_sim_time',
                default_value='true',
                description='Use the monotonic /clock from sensor fusion',
            ),
            DeclareLaunchArgument(
                'map',
                default_value=PathJoinSubstitution(
                    [navigation_share, 'maps', 'room.yaml']
                ),
                description='Absolute path to the static map YAML file',
            ),
            DeclareLaunchArgument(
                'nav_params_file',
                default_value=PathJoinSubstitution(
                    [navigation_share, 'param', 'xuegebot.yaml']
                ),
                description='Absolute path to the Nav2 parameter file',
            ),
            DeclareLaunchArgument(
                'autostart',
                default_value='true',
                description='Automatically activate Nav2 lifecycle nodes',
            ),
            DeclareLaunchArgument(
                'rviz',
                default_value='true',
                description='Start RViz with Navigation2',
            ),
            DeclareLaunchArgument(
                'web_port',
                default_value='8000',
                description='Web GUI port',
            ),
            IncludeLaunchDescription(
                _launch_file('xuegecar_bringup', 'control_core.launch.py'),
                launch_arguments={
                    'use_collision_monitor': LaunchConfiguration(
                        'use_collision_monitor'
                    ),
                    'use_sim_time': LaunchConfiguration('use_sim_time'),
                }.items(),
            ),
            IncludeLaunchDescription(
                _launch_file('xuegecar_agent_bridge', 'gateway.launch.py'),
                launch_arguments={
                    'launch_control_core': 'false',
                    'use_collision_monitor': LaunchConfiguration(
                        'use_collision_monitor'
                    ),
                    'use_sim_time': LaunchConfiguration('use_sim_time'),
                }.items(),
                condition=IfCondition(LaunchConfiguration('launch_gateway')),
            ),
            IncludeLaunchDescription(
                _launch_file('xuegecar_navigation2', 'navigation2.launch.py'),
                launch_arguments={
                    'launch_control_core': 'false',
                    'use_collision_monitor': LaunchConfiguration(
                        'use_collision_monitor'
                    ),
                    'map': LaunchConfiguration('map'),
                    'params_file': LaunchConfiguration('nav_params_file'),
                    'use_sim_time': LaunchConfiguration('use_sim_time'),
                    'autostart': LaunchConfiguration('autostart'),
                    'rviz': LaunchConfiguration('rviz'),
                }.items(),
                condition=IfCondition(LaunchConfiguration('launch_navigation')),
            ),
            IncludeLaunchDescription(
                _launch_file('xuegecar_web_gui', 'xuegecar_web_gui.launch.py'),
                launch_arguments={
                    'launch_twist_mux': 'false',
                    'include_camera': 'false',
                    'port': LaunchConfiguration('web_port'),
                }.items(),
                condition=IfCondition(LaunchConfiguration('launch_web_gui')),
            ),
        ]
    )
