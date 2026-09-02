"""XuegeCar web 上位机 launch。

独立模式（默认）：包含共享 control_core。
与完整控制栈同跑：由顶层传 launch_twist_mux:=false，避免重复启动 control_core。
"""

from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.actions import IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    package_share = Path(get_package_share_directory("xuegecar_web_gui"))
    camera_package_share = get_package_share_directory("xuegecar_camera")

    port = LaunchConfiguration("port")
    launch_twist_mux = LaunchConfiguration("launch_twist_mux")
    include_camera = LaunchConfiguration("include_camera")

    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "port",
                default_value="8000",
                description="Web 服务端口，手机浏览器访问 http://<主机IP>:<port>",
            ),
            DeclareLaunchArgument(
                "launch_twist_mux",
                default_value="true",
                description="兼容参数：独立运行时启动共享 control_core",
            ),
            DeclareLaunchArgument(
                "use_collision_monitor",
                default_value="true",
                description="在共享 control_core 中启用碰撞过滤",
            ),
            DeclareLaunchArgument(
                "use_sim_time",
                default_value="true",
                description="使用传感器融合提供的单调 /clock",
            ),
            DeclareLaunchArgument(
                "include_camera",
                default_value="false",
                description="顺带启动 xuegecar_camera 的 http_video_publisher",
            ),
            DeclareLaunchArgument(
                "camera_url",
                default_value="",
                description="IP 摄像头 MJPEG URL（include_camera 时传给摄像头节点）",
            ),
            DeclareLaunchArgument(
                "params_file",
                default_value=str(package_share / "config" / "xuegecar_web_gui.yaml"),
                description="Web 上位机参数文件",
            ),
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(
                    str(
                        Path(get_package_share_directory("xuegecar_bringup"))
                        / "launch"
                        / "control_core.launch.py"
                    )
                ),
                launch_arguments={
                    "use_collision_monitor": LaunchConfiguration(
                        "use_collision_monitor"
                    ),
                    "use_sim_time": LaunchConfiguration("use_sim_time"),
                }.items(),
                condition=IfCondition(launch_twist_mux),
            ),
            Node(
                package="xuegecar_web_gui",
                executable="web_gui_node",
                name="xuegecar_web_gui",
                output="screen",
                parameters=[
                    LaunchConfiguration("params_file"),
                    {"port": ParameterValue(port, value_type=int)},
                ],
            ),
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(
                    str(Path(camera_package_share) / "launch" / "http_video_publisher.launch.py")
                ),
                launch_arguments={"url": LaunchConfiguration("camera_url")}.items(),
                condition=IfCondition(include_camera),
            ),
        ]
    )
