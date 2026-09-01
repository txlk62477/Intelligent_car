from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from ament_index_python.packages import get_package_share_directory

import os


def generate_launch_description():
    share_dir = get_package_share_directory("camsense_lidar")
    parameter_file = LaunchConfiguration("params_file")
    use_sim_time = LaunchConfiguration("use_sim_time")
    use_sim_time_parameter = ParameterValue(use_sim_time, value_type=bool)

    params_declare = DeclareLaunchArgument(
        "params_file",
        default_value=os.path.join(share_dir, "params", "camsense_lidar.yaml"),
        description="Path to the ROS2 parameters file to use.",
    )
    use_sim_time_declare = DeclareLaunchArgument(
        "use_sim_time",
        default_value="true",
        description="Use the monotonic /clock from sensor fusion.",
    )

    driver_node = Node(
        package="camsense_lidar",
        executable="camsense_lidar_node",
        name="camsense_lidar_node",
        output="screen",
        emulate_tty=True,
        parameters=[parameter_file, {"use_sim_time": use_sim_time_parameter}],
        namespace="/",
    )

    tf2_node = Node(
        package="tf2_ros",
        executable="static_transform_publisher",
        name="static_tf_pub_laser",
        arguments=[
            "--x",
            "0",
            "--y",
            "0",
            "--z",
            "0.075",
            "--qx",
            "0",
            "--qy",
            "0",
            "--qz",
            "0",
            "--qw",
            "1",
            "--frame-id",
            "base_link",
            "--child-frame-id",
            "laser_frame",
        ],
    )

    return LaunchDescription([
        params_declare,
        use_sim_time_declare,
        driver_node,
        tf2_node,
    ])
