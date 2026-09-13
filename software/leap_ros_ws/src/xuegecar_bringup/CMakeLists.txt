cmake_minimum_required(VERSION 3.8)
project(xuegecar_bringup)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

# find dependencies
find_package(ament_cmake REQUIRED)
find_package(nav2_collision_monitor REQUIRED)
find_package(nav_msgs REQUIRED)
add_library(safe_collision_monitor src/safe_collision_monitor.cpp)
target_compile_features(safe_collision_monitor PUBLIC cxx_std_17)
target_include_directories(safe_collision_monitor PUBLIC include)
ament_target_dependencies(safe_collision_monitor nav2_collision_monitor nav_msgs)
add_executable(collision_monitor src/collision_monitor_main.cpp)
target_link_libraries(collision_monitor safe_collision_monitor)
install(TARGETS collision_monitor safe_collision_monitor
  RUNTIME DESTINATION lib/${PROJECT_NAME}
  LIBRARY DESTINATION lib
  ARCHIVE DESTINATION lib
)
install(
  DIRECTORY config launch
  DESTINATION share/${PROJECT_NAME}
  PATTERN "__pycache__" EXCLUDE
)

if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  set(ament_cmake_copyright_FOUND TRUE)
  set(ament_cmake_cpplint_FOUND TRUE)
  find_package(ament_cmake_pytest REQUIRED)
  find_package(ament_cmake_gtest REQUIRED)
  ament_add_gtest(collision_monitor_escape test/test_collision_monitor_escape.cpp)
  target_link_libraries(collision_monitor_escape safe_collision_monitor)
  target_compile_definitions(collision_monitor_escape PRIVATE
    COLLISION_CONFIG="${CMAKE_CURRENT_SOURCE_DIR}/config/collision_monitor.yaml"
    COLLISION_FIXTURES="${CMAKE_CURRENT_SOURCE_DIR}/test/fixtures"
  )
  set_tests_properties(collision_monitor_escape PROPERTIES
    ENVIRONMENT "ROS_DOMAIN_ID=183;ROS_LOCALHOST_ONLY=1;ROS_LOG_DIR=${CMAKE_CURRENT_BINARY_DIR}/ros_test_logs"
  )
  ament_add_pytest_test(
    control_launch_contract
    test/test_control_launch_contract.py
  )
  ament_add_pytest_test(
    robot_geometry_contract
    test/test_robot_geometry_contract.py
  )
  ament_lint_auto_find_test_dependencies()
endif()

ament_package()
