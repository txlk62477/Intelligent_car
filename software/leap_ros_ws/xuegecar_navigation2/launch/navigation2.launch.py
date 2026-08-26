import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

def generate_launch_description():
    xuegecar_navigation2_dir = get_package_share_directory('xuegecar_navigation2')
    nav2_bringup_dir = get_package_share_directory('nav2_bringup')

    # 定义参数
    use_sim_time = LaunchConfiguration('use_sim_time', default='false')
    map_yaml_path = LaunchConfiguration('map', default=os.path.join(xuegecar_navigation2_dir, 'maps', 'room.yaml'))
    nav2_param_path = LaunchConfiguration('params_file', default=os.path.join(xuegecar_navigation2_dir, 'param', 'xuegebot.yaml'))

    return LaunchDescription([
        # 声明启动参数
        DeclareLaunchArgument('use_sim_time', default_value=use_sim_time, description='Use simulation (Gazebo) clock if true'),
        DeclareLaunchArgument('map', default_value=map_yaml_path, description='Full path to map file to load'),
        DeclareLaunchArgument('params_file', default_value=nav2_param_path, description='Full path to param file to load'),

        # 包含 Nav2 的 bringup 启动文件
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource([nav2_bringup_dir, '/launch', '/bringup_launch.py']),
            launch_arguments={
                'map': map_yaml_path,
                'use_sim_time': use_sim_time,
                'params_file': nav2_param_path
            }.items(),
        ),
    ])