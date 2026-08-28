cmake_minimum_required(VERSION 3.8)
project(xuegecar_sensor_fusion)

if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(diagnostic_msgs REQUIRED)
find_package(nav_msgs REQUIRED)
find_package(rclcpp REQUIRED)
find_package(sensor_msgs REQUIRED)
find_package(std_msgs REQUIRED)

add_library(sensor_gate src/sensor_gate.cpp)
target_compile_features(sensor_gate PUBLIC cxx_std_17)
target_include_directories(sensor_gate PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>)

add_executable(sensor_gate_node src/sensor_gate_node.cpp)
target_compile_features(sensor_gate_node PUBLIC cxx_std_17)
target_link_libraries(sensor_gate_node sensor_gate)
ament_target_dependencies(sensor_gate_node
  diagnostic_msgs
  nav_msgs
  rclcpp
  sensor_msgs
  std_msgs)

install(TARGETS sensor_gate sensor_gate_node
  ARCHIVE DESTINATION lib
  LIBRARY DESTINATION lib
  RUNTIME DESTINATION lib/${PROJECT_NAME})

install(DIRECTORY include/
  DESTINATION include)

install(DIRECTORY config launch
  DESTINATION share/${PROJECT_NAME})

if(BUILD_TESTING)
  find_package(ament_cmake_gtest REQUIRED)
  ament_add_gtest(test_sensor_gate test/test_sensor_gate.cpp)
  target_link_libraries(test_sensor_gate sensor_gate)
endif()

ament_export_include_directories(include)
ament_export_libraries(sensor_gate)
ament_package()
