from launch import LaunchDescription
from launch.actions import ExecuteProcess, TimerAction
from launch_ros.actions import Node

def generate_launch_description():
    sudo_password = "xuegeros"
    
    # 1. Socat 进程 (立即启动)
    socat_cmd = f'echo "{sudo_password}" | sudo -S socat -d -d PTY,link=/dev/lidar,raw,echo=0,mode=666 UDP4-LISTEN:8889,reuseaddr,fork'
    socat_process = ExecuteProcess(cmd=[socat_cmd], shell=True, output='screen')

    # 2. Micro-ROS Agent (立即启动)
    micro_ros_agent = Node(
        package='micro_ros_agent',
        executable='micro_ros_agent',
        arguments=['udp4', '--port', '8888', '-v6'],
        output='screen'
    )

    # 3. Xuegecar (延迟 7 秒启动)
    xuegecar_launch = TimerAction(
        period=7.0,
        actions=[
            ExecuteProcess(
                cmd=['ros2', 'launch', 'xuegecar_bringup', 'xuegecar_bringup.launch.py'],
                output='screen'
            )
        ]
    )

    return LaunchDescription([
        socat_process,
        micro_ros_agent,
        xuegecar_launch
    ])
