cmake_minimum_required(VERSION 3.8)
project(xuegecar_qt_gui)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)

find_package(ament_cmake REQUIRED)
find_package(Qt5 REQUIRED COMPONENTS Widgets Gui Core)
find_package(rclcpp REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(nav_msgs REQUIRED)
find_package(sensor_msgs REQUIRED)
find_package(std_msgs REQUIRED)
find_package(tf2 REQUIRED)
find_package(tf2_geometry_msgs REQUIRED)
find_package(tf2_ros REQUIRED)

set(SOURCES
  include/xuegecar_qt_gui/main_window.hpp
  include/xuegecar_qt_gui/map_view.hpp
  include/xuegecar_qt_gui/lidar_view.hpp
  include/xuegecar_qt_gui/camera_view.hpp
  src/main.cpp
  src/main_window.cpp
  src/ros_interface.cpp
  src/map_view.cpp
  src/lidar_view.cpp
  src/camera_view.cpp
)

add_executable(xuegecar_qt_gui ${SOURCES})
target_include_directories(xuegecar_qt_gui PRIVATE include)
target_link_libraries(xuegecar_qt_gui Qt5::Widgets Qt5::Gui Qt5::Core)
ament_target_dependencies(xuegecar_qt_gui
  rclcpp
  geometry_msgs
  nav_msgs
  sensor_msgs
  std_msgs
  tf2
  tf2_geometry_msgs
  tf2_ros
)

install(TARGETS xuegecar_qt_gui
  DESTINATION lib/${PROJECT_NAME}
)

install(DIRECTORY launch
  DESTINATION share/${PROJECT_NAME}
)

install(FILES package.xml README.md
  DESTINATION share/${PROJECT_NAME}
)

ament_package()
