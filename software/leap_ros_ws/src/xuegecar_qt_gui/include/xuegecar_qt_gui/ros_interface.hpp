#pragma once

#include "xuegecar_qt_gui/types.hpp"

#include <geometry_msgs/msg/pose_with_covariance_stamped.hpp>
#include <geometry_msgs/msg/twist.hpp>
#include <nav_msgs/msg/occupancy_grid.hpp>
#include <nav_msgs/msg/odometry.hpp>
#include <nav_msgs/msg/path.hpp>
#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/battery_state.hpp>
#include <sensor_msgs/msg/compressed_image.hpp>
#include <sensor_msgs/msg/laser_scan.hpp>
#include <std_msgs/msg/float32.hpp>
#include <tf2_ros/buffer.h>
#include <tf2_ros/transform_listener.h>

#include <atomic>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace xuegecar_qt_gui
{

class RosInterface : public rclcpp::Node
{
public:
  RosInterface();
  ~RosInterface() override;

  void start();
  void stop();
  AppState stateSnapshot() const;

  void publishVelocity(double linear, double angular);
  void publishGoal(double x, double y, double yaw);
  void publishInitialPose(double x, double y, double yaw);

private:
  void spinLoop();
  void stampSeen(const std::string & name);
  double topicAge(const std::string & name, const rclcpp::Time & now) const;
  Pose2D transformPose(const std::string & frame_id, double x, double y, double yaw) const;

  void onCamera(const sensor_msgs::msg::CompressedImage::SharedPtr msg);
  void onScan(const sensor_msgs::msg::LaserScan::SharedPtr msg);
  void onMap(const nav_msgs::msg::OccupancyGrid::SharedPtr msg);
  void onCostmap(const nav_msgs::msg::OccupancyGrid::SharedPtr msg, const std::string & label);
  void onPath(const nav_msgs::msg::Path::SharedPtr msg, const std::string & label);
  void onOdom(const nav_msgs::msg::Odometry::SharedPtr msg);
  void onBattery(const sensor_msgs::msg::BatteryState::SharedPtr msg);
  void onVoltage(double value);
  void onPercent(double value);

  MapLayer occupancyToMapLayer(const nav_msgs::msg::OccupancyGrid & msg, bool costmap) const;

  mutable std::mutex state_mutex_;
  AppState state_;
  std::map<std::string, rclcpp::Time> last_seen_;

  rclcpp::Publisher<geometry_msgs::msg::Twist>::SharedPtr cmd_pub_;
  rclcpp::Publisher<geometry_msgs::msg::PoseStamped>::SharedPtr goal_pub_;
  rclcpp::Publisher<geometry_msgs::msg::PoseWithCovarianceStamped>::SharedPtr initial_pose_pub_;
  rclcpp::Subscription<sensor_msgs::msg::CompressedImage>::SharedPtr camera_sub_;
  rclcpp::Subscription<sensor_msgs::msg::LaserScan>::SharedPtr scan_sub_;
  rclcpp::Subscription<nav_msgs::msg::OccupancyGrid>::SharedPtr map_sub_;
  rclcpp::Subscription<nav_msgs::msg::OccupancyGrid>::SharedPtr map_volatile_sub_;
  rclcpp::Subscription<nav_msgs::msg::Odometry>::SharedPtr odom_sub_;
  rclcpp::Subscription<sensor_msgs::msg::BatteryState>::SharedPtr battery_sub_;
  std::vector<rclcpp::Subscription<nav_msgs::msg::Path>::SharedPtr> path_subs_;
  std::vector<rclcpp::Subscription<nav_msgs::msg::OccupancyGrid>::SharedPtr> costmap_subs_;
  std::vector<rclcpp::Subscription<std_msgs::msg::Float32>::SharedPtr> float32_subs_;

  std::shared_ptr<tf2_ros::Buffer> tf_buffer_;
  std::shared_ptr<tf2_ros::TransformListener> tf_listener_;
  rclcpp::executors::SingleThreadedExecutor executor_;
  std::thread spin_thread_;
  std::atomic_bool running_{false};

  std::string fixed_frame_{"map"};
  std::string base_frame_{"base_link"};
  std::string fallback_base_frame_{"base_footprint"};
};

}  // namespace xuegecar_qt_gui
