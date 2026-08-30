import os
from glob import glob

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction, SetEnvironmentVariable
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def _nvidia_lib_paths() -> list[str]:
    """返回工作区 venv 里 pip 安装的 nvidia CUDA 库目录（onnxruntime-gpu 需要）。"""
    return sorted(
        glob(
            "/home/lk/car/software/leap_ros_ws/src/xuegecar_perception/.venv/lib/python*/"
            "site-packages/nvidia/*/lib"
        )
    )


def _launch_nodes(context, *args, **kwargs) -> list[Node]:
    params_file = context.perform_substitution(LaunchConfiguration("params_file"))
    use_sim = context.perform_substitution(LaunchConfiguration("sim")).strip().lower() in (
        "1", "true", "yes", "on"
    )
    # publish_annotated_raw: 空字符串=不覆盖，使用 params_file 里的值；否则按布尔覆盖。
    raw_annotated = context.perform_substitution(
        LaunchConfiguration("publish_annotated_raw")
    ).strip()

    yolo_params = [params_file]
    if raw_annotated:
        yolo_params.append(
            {"publish_annotated_raw": raw_annotated.lower() in ("1", "true", "yes", "on")}
        )

    nodes = [
        Node(
            package="xuegecar_perception",
            executable="yolo_detect_node",
            name="yolo_detect_node",
            output="screen",
            emulate_tty=True,
            parameters=yolo_params,
        ),
    ]
    if use_sim:
        nodes.insert(
            0,
            Node(
                package="xuegecar_perception",
                executable="sim_camera_publisher",
                name="sim_camera_publisher",
                output="screen",
                emulate_tty=True,
                parameters=[params_file],
            ),
        )
    return nodes


def generate_launch_description() -> LaunchDescription:
    package_dir = get_package_share_directory("xuegecar_perception")
    default_params_file = os.path.join(package_dir, "config", "perception.yaml")

    # 追加而不是覆盖：PYTHONPATH 必须保留 /opt/ros/jazzy，否则 rclpy 找不到。
    venv_site_packages = (
        "/home/lk/car/software/leap_ros_ws/src/xuegecar_perception/.venv/"
        "lib/python3.12/site-packages"
    )
    inherited_pythonpath = os.environ.get("PYTHONPATH", "")
    pythonpath = venv_site_packages
    if inherited_pythonpath:
        pythonpath = f"{venv_site_packages}:{inherited_pythonpath}"

    nvidia_libs = _nvidia_lib_paths()
    inherited_ld_library_path = os.environ.get("LD_LIBRARY_PATH", "")
    ld_library_path = inherited_ld_library_path
    if nvidia_libs:
        ld_library_path = ":".join(nvidia_libs)
        if inherited_ld_library_path:
            ld_library_path = f"{ld_library_path}:{inherited_ld_library_path}"

    environment = [
        SetEnvironmentVariable("PYTHONPATH", pythonpath),
    ]
    if ld_library_path:
        environment.append(SetEnvironmentVariable("LD_LIBRARY_PATH", ld_library_path))

    return LaunchDescription([
        *environment,
        DeclareLaunchArgument(
            "params_file",
            default_value=default_params_file,
            description="Path to YAML file with perception parameters.",
        ),
        DeclareLaunchArgument(
            "sim",
            default_value="false",
            description="Start the simulated camera publisher when true; "
            "default false to detect the real camera node's topic.",
        ),
        DeclareLaunchArgument(
            "publish_annotated_raw",
            default_value="",
            description="When set (true/false), override whether the raw "
            "sensor_msgs/Image annotated topic /vision/annotated is published. "
            "Empty uses the params_file value (default off).",
        ),
        OpaqueFunction(function=_launch_nodes),
    ])
