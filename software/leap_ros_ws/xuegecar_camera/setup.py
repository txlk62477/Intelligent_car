from glob import glob
import os

from setuptools import find_packages, setup

package_name = "xuegecar_camera"

setup(
    name=package_name,
    version="0.0.1",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (f"share/{package_name}", ["package.xml"]),
        (os.path.join("share", package_name, "launch"), glob("launch/*.launch.py")),
        (os.path.join("share", package_name, "config"), glob("config/*.yaml")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="xuegeros",
    maintainer_email="xuegeros@todo.todo",
    description="ROS2 HTTP video stream publisher for xuegecar.",
    license="MIT",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "http_video_publisher = xuegecar_camera.http_video_publisher:main",
        ],
    },
)
