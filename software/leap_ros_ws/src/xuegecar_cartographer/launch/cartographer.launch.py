from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition, UnlessCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    """Start Cartographer, consuming existing fusion and /scan by default."""
    package_share = Path(get_package_share_directory("xuegecar_cartographer"))
    fusion_share = Path(get_package_share_directory("xuegecar_sensor_fusion"))
    lidar_share = Path(get_package_share_directory("camsense_lidar"))

    use_sim_time = LaunchConfiguration("use_sim_time")
    start_fusion = LaunchConfiguration("start_fusion")
    start_lidar = LaunchConfiguration("start_lidar")
    start_rviz = LaunchConfiguration("start_rviz")
    configuration_directory = LaunchConfiguration("configuration_directory")
    configuration_basename = LaunchConfiguration("configuration_basename")
    resolution = LaunchConfiguration("resolution")
    publish_period_sec = LaunchConfiguration("publish_period_sec")
    use_sim_time_parameter = ParameterValue(use_sim_time, value_type=bool)

    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "use_sim_time",
                default_value="true",
                description="Use the monotonic /clock from sensor fusion",
            ),
            DeclareLaunchArgument(
                "start_fusion",
                default_value="false",
                description="Optionally start monotonic clock, sensor gate, and EKF",
            ),
            DeclareLaunchArgument(
                "start_lidar",
                default_value="false",
                description="Optionally start the local Camsense /scan publisher",
            ),
            DeclareLaunchArgument(
                "start_rviz",
                default_value="true",
                description="Start RViz with the mapping configuration",
            ),
            DeclareLaunchArgument(
                "configuration_directory",
                default_value=str(package_share / "config"),
                description="Cartographer Lua configuration directory",
            ),
            DeclareLaunchArgument(
                "configuration_basename",
                default_value="xuegecar_2d.lua",
                description="Cartographer Lua configuration filename",
            ),
            DeclareLaunchArgument("resolution", default_value="0.05"),
            DeclareLaunchArgument("publish_period_sec", default_value="1.0"),
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(
                    str(fusion_share / "launch" / "fusion.launch.py")
                ),
                launch_arguments={"use_sim_time": use_sim_time}.items(),
                condition=IfCondition(start_fusion),
            ),
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(
                    str(lidar_share / "launch" / "camsense_lidar.launch.py")
                ),
                launch_arguments={"use_sim_time": use_sim_time}.items(),
                condition=IfCondition(start_lidar),
            ),
            # The MCU already publishes /scan with frame_id=laser_frame.  When
            # the optional local driver is disabled, publish only its fixed
            # mounting transform here (without starting another /scan source).
            Node(
                package="tf2_ros",
                executable="static_transform_publisher",
                name="static_tf_pub_laser_mapping",
                arguments=[
                    "--x", "0", "--y", "0", "--z", "0.075",
                    "--qx", "0", "--qy", "0", "--qz", "0", "--qw", "1",
                    "--frame-id", "base_link",
                    "--child-frame-id", "laser_frame",
                ],
                condition=UnlessCondition(start_lidar),
            ),
            Node(
                package="cartographer_ros",
                executable="cartographer_node",
                name="cartographer_node",
                output="screen",
                parameters=[{"use_sim_time": use_sim_time_parameter}],
                arguments=[
                    "-configuration_directory",
                    configuration_directory,
                    "-configuration_basename",
                    configuration_basename,
                ],
                remappings=[("odom", "/odometry/filtered")],
            ),
            Node(
                package="cartographer_ros",
                executable="cartographer_occupancy_grid_node",
                name="cartographer_occupancy_grid_node",
                output="screen",
                parameters=[{"use_sim_time": use_sim_time_parameter}],
                arguments=[
                    "-resolution",
                    resolution,
                    "-publish_period_sec",
                    publish_period_sec,
                ],
            ),
            Node(
                package="rviz2",
                executable="rviz2",
                name="rviz2",
                arguments=[
                    "-d",
                    str(package_share / "rviz" / "xuegecar_cartographer.rviz"),
                ],
                parameters=[{"use_sim_time": use_sim_time_parameter}],
                output="screen",
                condition=IfCondition(start_rviz),
            ),
        ]
    )
