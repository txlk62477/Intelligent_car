from glob import glob
import os

from setuptools import find_packages, setup


package_name = "xuegecar_agent_bridge"

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
    description="Safe HTTP gateway between LangGraph and xuegecar ROS2 topics.",
    license="GPL-2.0-only",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "gateway = xuegecar_agent_bridge.node:main",
        ],
    },
)
