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
    
    # 获取 slam_gmapping 包的路径
    slam_gmapping_dir = get_package_share_directory('slam_gmapping')

    use_sim_time = LaunchConfiguration('use_sim_time', default='false')
    map_yaml_path = LaunchConfiguration('map',default=os.path.join(xuegecar_navigation2_dir,'maps','room.yaml'))
    nav2_param_path = LaunchConfiguration('params_file',default=os.path.join(xuegecar_navigation2_dir,'param','xuegebot.yaml'))

    rviz_config_dir = os.path.join(nav2_bringup_dir,'rviz','nav2_default_view.rviz')

    return LaunchDescription([
        DeclareLaunchArgument('use_sim_time',default_value=use_sim_time,description='Use simulation (Gazebo) clock if true'),
        DeclareLaunchArgument('params_file',default_value=nav2_param_path,description='Full path to param file to load'),

        # 包含 slam_gmapping 启动文件
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                os.path.join(slam_gmapping_dir, 'launch', 'slam_gmapping.launch.py')
            ),
            launch_arguments={
                'use_sim_time': use_sim_time
            }.items()
        ),

        # 包含 nav2_bringup
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource([nav2_bringup_dir,'/launch','/bringup_launch.py']),
            launch_arguments={
                'map': map_yaml_path,
                'use_sim_time': use_sim_time,
                'params_file': nav2_param_path}.items(),
        ),
        
        Node(
            package='rviz2',
            executable='rviz2',
            name='rviz2',
            arguments=['-d', rviz_config_dir],
            parameters=[{'use_sim_time': use_sim_time}],
            output='screen'),
    ])
