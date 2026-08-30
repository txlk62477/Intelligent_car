cmake_minimum_required(VERSION 3.8)
project(leap_interfaces)

find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(std_msgs REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "action/ExecuteMotion.action"
  "action/FollowTarget.action"
  "msg/Detection.msg"
  "msg/Detections.msg"
  "srv/GetSpeedPid.srv"
  "srv/SetPerception.srv"
  "srv/SetSpeedPid.srv"
  DEPENDENCIES std_msgs
)

ament_export_dependencies(rosidl_default_runtime)
ament_package()
