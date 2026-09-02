cmake_minimum_required(VERSION 3.8)
project(xuegecar_bringup)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

# find dependencies
find_package(ament_cmake REQUIRED)
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
