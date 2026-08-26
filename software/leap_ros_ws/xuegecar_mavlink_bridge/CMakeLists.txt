cmake_minimum_required(VERSION 3.8)
project(xuegecar_mavlink_bridge)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra)
endif()

find_package(ament_cmake REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(nav_msgs REQUIRED)
find_package(rclcpp REQUIRED)
find_package(sensor_msgs REQUIRED)

set(dependencies
  geometry_msgs
  nav_msgs
  rclcpp
  sensor_msgs
)

add_executable(mavlink_bridge src/mavlink_bridge.cpp)
target_compile_features(mavlink_bridge PUBLIC cxx_std_17)
target_include_directories(mavlink_bridge SYSTEM PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}/include/mavlink_lib
)
ament_target_dependencies(mavlink_bridge
  ${dependencies}
)

install(TARGETS mavlink_bridge
  DESTINATION lib/${PROJECT_NAME}
)

install(
  DIRECTORY config launch
  DESTINATION share/${PROJECT_NAME}
)

if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  set(ament_cmake_copyright_FOUND TRUE)
  set(ament_cmake_cpplint_FOUND TRUE)
  ament_lint_auto_find_test_dependencies()
endif()

ament_package()
