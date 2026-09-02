"""Start only the XuegeCar robot and joint state publishers."""

from launch import LaunchDescription
from launch.substitutions import PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    urdf = PathJoinSubstitution(
        [FindPackageShare('xuegecar_description'), 'urdf', 'xuegecar.urdf']
    )

    return LaunchDescription(
        [
            Node(
                package='joint_state_publisher',
                executable='joint_state_publisher',
                name='joint_state_publisher',
                arguments=[urdf],
                output='screen',
            ),
            Node(
                package='robot_state_publisher',
                executable='robot_state_publisher',
                arguments=[urdf],
                output='screen',
            ),
        ]
    )
