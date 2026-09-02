from setuptools import find_packages, setup

package_name = "xuegecar_web_gui"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/launch", ["launch/xuegecar_web_gui.launch.py"]),
        ("share/" + package_name + "/config", [
            "config/xuegecar_web_gui.yaml",
        ]),
        (
            "lib/" + package_name + "/static",
            [
                "xuegecar_web_gui/static/index.html",
                "xuegecar_web_gui/static/app.js",
                "xuegecar_web_gui/static/style.css",
            ],
        ),
        (
            "lib/" + package_name + "/static/vendor",
            ["xuegecar_web_gui/static/vendor/nipplejs.min.js"],
        ),
    ],
    install_requires=["setuptools"],
    entry_points={
        "console_scripts": [
            "web_gui_node = xuegecar_web_gui.web:main",
        ],
    },
    zip_safe=True,
)
