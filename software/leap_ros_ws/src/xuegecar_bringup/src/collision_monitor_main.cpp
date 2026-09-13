// Shared control stack's collision-safety executable.
#include <memory>

#include "xuegecar_bringup/safe_collision_monitor.hpp"

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  auto node = std::make_shared<xuegecar_bringup::SafeCollisionMonitor>();
  rclcpp::spin(node->get_node_base_interface());
  rclcpp::shutdown();
  return 0;
}
