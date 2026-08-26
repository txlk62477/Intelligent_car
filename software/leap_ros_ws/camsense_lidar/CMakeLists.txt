cmake_minimum_required(VERSION 3.8)
project(camsense_lidar)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(sensor_msgs REQUIRED)

add_library(camsense_lidar_driver
  src/camsense_lidar.cpp
)
target_include_directories(camsense_lidar_driver PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>
)
target_compile_features(camsense_lidar_driver PUBLIC cxx_std_17)

add_executable(camsense_lidar_node
  src/camsense_lidar_node.cpp
)
target_link_libraries(camsense_lidar_node
  camsense_lidar_driver
)
ament_target_dependencies(camsense_lidar_node
  rclcpp
  sensor_msgs
)
target_compile_features(camsense_lidar_node PUBLIC cxx_std_17)

install(TARGETS
  camsense_lidar_driver
  camsense_lidar_node
  ARCHIVE DESTINATION lib
  LIBRARY DESTINATION lib
  RUNTIME DESTINATION lib/${PROJECT_NAME}
)

install(DIRECTORY include/
  DESTINATION include
)

install(DIRECTORY launch params
  DESTINATION share/${PROJECT_NAME}
)

if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  set(ament_cmake_copyright_FOUND TRUE)
  set(ament_cmake_cpplint_FOUND TRUE)
  ament_lint_auto_find_test_dependencies()
endif()

ament_package()
