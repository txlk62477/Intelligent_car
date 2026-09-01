"""Launch static-map localization and Nav2 for xuegecar."""

from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    package_share = Path(get_package_share_directory('xuegecar_navigation2'))

    map_yaml = LaunchConfiguration('map')
    params_file = LaunchConfiguration('params_file')
    mux_params_file = str(package_share / 'config' / 'twist_mux.yaml')
    use_sim_time = ParameterValue(
        LaunchConfiguration('use_sim_time'), value_type=bool
    )
    autostart = ParameterValue(LaunchConfiguration('autostart'), value_type=bool)

    common_parameters = [params_file, {'use_sim_time': use_sim_time}]
    tf_remappings = [('/tf', 'tf'), ('/tf_static', 'tf_static')]

    localization_nodes = ['map_server', 'amcl']
    navigation_nodes = [
        'controller_server',
        'smoother_server',
        'planner_server',
        'behavior_server',
        'bt_navigator',
        'waypoint_follower',
        'velocity_smoother',
        'collision_monitor',
    ]

    return LaunchDescription(
        [
            DeclareLaunchArgument(
                'map',
                default_value=str(package_share / 'maps' / 'room.yaml'),
                description='Absolute path to the static map YAML file',
            ),
            DeclareLaunchArgument(
                'params_file',
                default_value=str(package_share / 'param' / 'xuegebot.yaml'),
                description='Absolute path to the Nav2 parameter file',
            ),
            DeclareLaunchArgument(
                'use_sim_time',
                default_value='true',
                description='Use the monotonic /clock from sensor fusion',
            ),
            DeclareLaunchArgument(
                'autostart',
                default_value='true',
                description='Automatically activate Nav2 lifecycle nodes',
            ),
            DeclareLaunchArgument(
                'rviz',
                default_value='true',
                description='Start RViz with the xuegecar navigation view',
            ),
            Node(
                package='nav2_map_server',
                executable='map_server',
                name='map_server',
                output='screen',
                parameters=common_parameters + [{'yaml_filename': map_yaml}],
                remappings=tf_remappings,
            ),
            Node(
                package='nav2_amcl',
                executable='amcl',
                name='amcl',
                output='screen',
                parameters=common_parameters,
                remappings=tf_remappings,
            ),
            Node(
                package='nav2_lifecycle_manager',
                executable='lifecycle_manager',
                name='lifecycle_manager_localization',
                output='screen',
                parameters=[
                    {'use_sim_time': use_sim_time},
                    {'autostart': autostart},
                    {'node_names': localization_nodes},
                ],
            ),
            Node(
                package='nav2_controller',
                executable='controller_server',
                name='controller_server',
                output='screen',
                parameters=common_parameters,
                remappings=tf_remappings + [('cmd_vel', 'cmd_vel_nav_raw')],
            ),
            Node(
                package='nav2_smoother',
                executable='smoother_server',
                name='smoother_server',
                output='screen',
                parameters=common_parameters,
                remappings=tf_remappings,
            ),
            Node(
                package='nav2_planner',
                executable='planner_server',
                name='planner_server',
                output='screen',
                parameters=common_parameters,
                remappings=tf_remappings,
            ),
            Node(
                package='nav2_behaviors',
                executable='behavior_server',
                name='behavior_server',
                output='screen',
                parameters=common_parameters,
                remappings=tf_remappings + [('cmd_vel', 'cmd_vel_nav_raw')],
            ),
            Node(
                package='nav2_bt_navigator',
                executable='bt_navigator',
                name='bt_navigator',
                output='screen',
                parameters=common_parameters,
                remappings=tf_remappings,
            ),
            Node(
                package='nav2_waypoint_follower',
                executable='waypoint_follower',
                name='waypoint_follower',
                output='screen',
                parameters=common_parameters,
                remappings=tf_remappings,
            ),
            Node(
                package='nav2_velocity_smoother',
                executable='velocity_smoother',
                name='velocity_smoother',
                output='screen',
                parameters=common_parameters,
                remappings=tf_remappings
                + [
                    ('cmd_vel', 'cmd_vel_nav_raw'),
                    ('cmd_vel_smoothed', 'cmd_vel_nav'),
                ],
            ),
            Node(
                package='twist_mux',
                executable='twist_mux',
                name='twist_mux',
                output='screen',
                parameters=[mux_params_file, {'use_sim_time': use_sim_time}],
                remappings=[('/cmd_vel_out', '/cmd_vel_selected')],
            ),
            Node(
                package='nav2_collision_monitor',
                executable='collision_monitor',
                name='collision_monitor',
                output='screen',
                parameters=common_parameters,
                remappings=tf_remappings
                + [
                    ('cmd_vel_smoothed', '/cmd_vel_selected'),
                    ('cmd_vel', '/cmd_vel'),
                ],
            ),
            Node(
                package='nav2_lifecycle_manager',
                executable='lifecycle_manager',
                name='lifecycle_manager_navigation',
                output='screen',
                parameters=[
                    {'use_sim_time': use_sim_time},
                    {'autostart': autostart},
                    {'node_names': navigation_nodes},
                ],
            ),
            Node(
                package='rviz2',
                executable='rviz2',
                name='rviz2',
                output='screen',
                arguments=[
                    '-d',
                    str(package_share / 'rviz' / 'xuege_navigation2.rviz'),
                ],
                parameters=[{'use_sim_time': use_sim_time}],
                condition=IfCondition(LaunchConfiguration('rviz')),
            ),
        ]
    )
