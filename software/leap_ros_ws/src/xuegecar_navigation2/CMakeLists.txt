cmake_minimum_required(VERSION 3.5)
project(xuegecar_navigation2)

# Default to C99
if(NOT CMAKE_C_STANDARD)
  set(CMAKE_C_STANDARD 99)
endif()

# Default to C++14
if(NOT CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 14)
endif()

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

# find dependencies
find_package(ament_cmake REQUIRED)
# uncomment the following section in order to fill in
# further dependencies manually.
# find_package(<dependency> REQUIRED)



if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  # This legacy package does not yet declare a project-wide copyright/license.
  set(ament_cmake_copyright_FOUND TRUE)
  # Retained for source history, but no longer installed or supported.
  set(AMENT_LINT_AUTO_FILE_EXCLUDE
    launch/slam_nav2.launch.py
    maps/show_pgm.py
  )
  # the following line skips cpplint (only works in a git repo)
  # uncomment the line when this package is not in a git repo
  #set(ament_cmake_cpplint_FOUND TRUE)
  ament_lint_auto_find_test_dependencies()
endif()

install(
  FILES
    launch/navigation2.launch.py
    launch/navigation2_rviz.launch.py
  DESTINATION share/${PROJECT_NAME}/launch
)

install(
  FILES param/xuegebot.yaml
  DESTINATION share/${PROJECT_NAME}/param
)

install(
  DIRECTORY config
  DESTINATION share/${PROJECT_NAME}
)

install(
  DIRECTORY maps rviz
  DESTINATION share/${PROJECT_NAME}
)

ament_package()
