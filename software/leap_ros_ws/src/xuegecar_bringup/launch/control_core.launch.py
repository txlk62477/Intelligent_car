"""Start the single shared velocity-selection and safety pipeline."""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import IfElseSubstitution, LaunchConfiguration
from launch.substitutions import PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    use_collision_monitor = LaunchConfiguration('use_collision_monitor')
    use_sim_time = ParameterValue(
        LaunchConfiguration('use_sim_time'), value_type=bool
    )

    bringup_share = FindPackageShare('xuegecar_bringup')
    description_share = FindPackageShare('xuegecar_description')
    controller_share = FindPackageShare('xuegecar_motion_controller')

    urdf = PathJoinSubstitution(
        [description_share, 'urdf', 'xuegecar.urdf']
    )
    mux_params = PathJoinSubstitution(
        [bringup_share, 'config', 'twist_mux.yaml']
    )
    collision_params = PathJoinSubstitution(
        [bringup_share, 'config', 'collision_monitor.yaml']
    )
    controller_params = PathJoinSubstitution(
        [controller_share, 'config', 'controller.yaml']
    )

    mux_output = IfElseSubstitution(
        use_collision_monitor,
        if_value='/cmd_vel_selected',
        else_value='/cmd_vel',
    )

    return LaunchDescription(
        [
            DeclareLaunchArgument(
                'use_collision_monitor',
                default_value='true',
                description='Filter the selected velocity through Nav2 collision monitor',
            ),
            DeclareLaunchArgument(
                'use_sim_time',
                default_value='true',
                description='Use the monotonic /clock from sensor fusion',
            ),
            Node(
                package='robot_state_publisher',
                executable='robot_state_publisher',
                output='screen',
                arguments=[urdf],
                parameters=[{'use_sim_time': use_sim_time}],
            ),
            Node(
                package='joint_state_publisher',
                executable='joint_state_publisher',
                name='joint_state_publisher',
                output='screen',
                arguments=[urdf],
                parameters=[{'use_sim_time': use_sim_time}],
            ),
            Node(
                package='xuegecar_motion_controller',
                executable='motion_controller',
                name='xuegecar_motion_controller',
                output='screen',
                parameters=[controller_params, {'use_sim_time': use_sim_time}],
            ),
            Node(
                package='twist_mux',
                executable='twist_mux',
                name='twist_mux',
                output='screen',
                # Safety timeouts must keep advancing if the external /clock stops.
                parameters=[mux_params, {'use_sim_time': False}],
                remappings=[('/cmd_vel_out', mux_output)],
            ),
            Node(
                package='nav2_collision_monitor',
                executable='collision_monitor',
                name='collision_monitor',
                output='screen',
                parameters=[collision_params, {'use_sim_time': use_sim_time}],
                condition=IfCondition(use_collision_monitor),
            ),
            Node(
                package='nav2_lifecycle_manager',
                executable='lifecycle_manager',
                name='lifecycle_manager_collision_monitor',
                output='screen',
                parameters=[
                    {'use_sim_time': use_sim_time},
                    {'autostart': True},
                    {'node_names': ['collision_monitor']},
                ],
                condition=IfCondition(use_collision_monitor),
            ),
        ]
    )
