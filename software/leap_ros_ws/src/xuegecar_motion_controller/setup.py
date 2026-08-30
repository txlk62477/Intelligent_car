import os
from glob import glob

from setuptools import find_packages, setup

package_name = "xuegecar_motion_controller"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (f"share/{package_name}", ["package.xml"]),
        (os.path.join("share", package_name, "config"), glob("config/*.yaml")),
        (os.path.join("share", package_name, "launch"), glob("launch/*.launch.py")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="lk",
    maintainer_email="lk@example.com",
    description="Exclusive ROS2 motion and visual-follow controller for xuegecar.",
    license="GPL-2.0-only",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "motion_controller = xuegecar_motion_controller.node:main",
        ],
    },
)
