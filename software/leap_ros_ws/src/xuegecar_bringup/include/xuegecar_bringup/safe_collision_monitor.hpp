// Collision Monitor with a bounded straight-line escape from shallow overlap.
#ifndef XUEGECAR_BRINGUP__SAFE_COLLISION_MONITOR_HPP_
#define XUEGECAR_BRINGUP__SAFE_COLLISION_MONITOR_HPP_

#include <chrono>
#include <functional>
#include <memory>
#include <vector>

#include "nav2_collision_monitor/collision_monitor_node.hpp"
#include "nav_msgs/msg/odometry.hpp"

namespace xuegecar_bringup
{
class EscapeScan : public nav2_collision_monitor::Scan
{
public:
  using Scan::Scan;
  void configure();
  void receive(sensor_msgs::msg::LaserScan::ConstSharedPtr message);
  bool fresh() const;
  bool readRaw(const rclcpp::Time & time, std::vector<nav2_collision_monitor::Point> & points);
  bool getData(
    const rclcpp::Time & time,
    std::vector<nav2_collision_monitor::Point> & points) override;
  std::function<bool(const nav2_collision_monitor::Point &)> ignore;

private:
  std::chrono::steady_clock::time_point received_{};
};

class SafeCollisionMonitor : public nav2_collision_monitor::CollisionMonitor
{
public:
  explicit SafeCollisionMonitor(const rclcpp::NodeOptions & options = rclcpp::NodeOptions());

protected:
  nav2_util::CallbackReturn on_configure(const rclcpp_lifecycle::State & state) override;
  nav2_util::CallbackReturn on_cleanup(const rclcpp_lifecycle::State & state) override;
  void processSafe(
    const nav2_collision_monitor::Velocity & velocity,
    const std_msgs::msg::Header & header);
  void receiveOdometry(nav_msgs::msg::Odometry::ConstSharedPtr message);
  void watchdog();
  std::shared_ptr<EscapeScan> escape_scan_;

private:
  void receiveCommand(geometry_msgs::msg::TwistStamped::SharedPtr message);
  void setEscape(int side);
  bool stopped() const;
  bool inside(const nav2_collision_monitor::Point & point) const;
  double front_{0.0}, rear_{0.0}, left_{0.0}, right_{0.0};
  double padding_{0.0}, overlap_{0.0}, escape_speed_{0.0};
  double odom_timeout_{0.0}, command_timeout_{0.0};
  double stopped_linear_{0.0}, stopped_angular_{0.0};
  int min_points_{0}, escape_side_{0};
  nav_msgs::msg::Odometry::ConstSharedPtr odometry_;
  std::chrono::steady_clock::time_point odom_received_{}, command_received_{};
  rclcpp::Subscription<nav_msgs::msg::Odometry>::SharedPtr odom_subscription_;
  rclcpp::TimerBase::SharedPtr watchdog_timer_;
};
}  // namespace xuegecar_bringup
#endif  // XUEGECAR_BRINGUP__SAFE_COLLISION_MONITOR_HPP_
