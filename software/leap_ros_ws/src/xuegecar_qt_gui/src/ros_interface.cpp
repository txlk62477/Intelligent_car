#include "xuegecar_qt_gui/ros_interface.hpp"

#include <QImage>

#include <geometry_msgs/msg/pose_stamped.hpp>
#include <tf2/LinearMath/Quaternion.h>
#include <tf2/utils.h>
#include <tf2_geometry_msgs/tf2_geometry_msgs.hpp>

#include <algorithm>
#include <cmath>
#include <limits>

namespace xuegecar_qt_gui
{
namespace
{
double normalizeAngle(double angle)
{
  while (angle > M_PI) {
    angle -= 2.0 * M_PI;
  }
  while (angle < -M_PI) {
    angle += 2.0 * M_PI;
  }
  return angle;
}

geometry_msgs::msg::Quaternion yawToQuaternion(double yaw)
{
  tf2::Quaternion q;
  q.setRPY(0.0, 0.0, yaw);
  return tf2::toMsg(q);
}
}  // namespace

RosInterface::RosInterface()
: Node("xuegecar_qt_gui")
{
  declare_parameter("camera_topic", "/camera/image_raw/compressed");
  declare_parameter("scan_topic", "/scan");
  declare_parameter("map_topic", "/map");
  declare_parameter("odom_topic", "/odom");
  declare_parameter("cmd_vel_topic", "/cmd_vel");
  declare_parameter("goal_topic", "/goal_pose");
  declare_parameter("fixed_frame", "map");
  declare_parameter("base_frame", "base_link");
  declare_parameter("fallback_base_frame", "base_footprint");

  const auto camera_topic = get_parameter("camera_topic").as_string();
  const auto scan_topic = get_parameter("scan_topic").as_string();
  const auto map_topic = get_parameter("map_topic").as_string();
  const auto odom_topic = get_parameter("odom_topic").as_string();
  const auto cmd_vel_topic = get_parameter("cmd_vel_topic").as_string();
  const auto goal_topic = get_parameter("goal_topic").as_string();
  fixed_frame_ = get_parameter("fixed_frame").as_string();
  base_frame_ = get_parameter("base_frame").as_string();
  fallback_base_frame_ = get_parameter("fallback_base_frame").as_string();

  auto sensor_qos = rclcpp::SensorDataQoS().keep_last(1);
  auto latest_qos = rclcpp::QoS(rclcpp::KeepLast(1)).best_effort();
  auto map_qos = rclcpp::QoS(rclcpp::KeepLast(1)).reliable().transient_local();
  auto map_volatile_qos = rclcpp::QoS(rclcpp::KeepLast(1)).reliable();

  tf_buffer_ = std::make_shared<tf2_ros::Buffer>(get_clock());
  tf_listener_ = std::make_shared<tf2_ros::TransformListener>(*tf_buffer_);

  cmd_pub_ = create_publisher<geometry_msgs::msg::Twist>(cmd_vel_topic, 10);
  goal_pub_ = create_publisher<geometry_msgs::msg::PoseStamped>(goal_topic, 10);
  initial_pose_pub_ =
    create_publisher<geometry_msgs::msg::PoseWithCovarianceStamped>("/initialpose", 10);

  camera_sub_ = create_subscription<sensor_msgs::msg::CompressedImage>(
    camera_topic, sensor_qos, std::bind(&RosInterface::onCamera, this, std::placeholders::_1));
  scan_sub_ = create_subscription<sensor_msgs::msg::LaserScan>(
    scan_topic, sensor_qos, std::bind(&RosInterface::onScan, this, std::placeholders::_1));
  map_sub_ = create_subscription<nav_msgs::msg::OccupancyGrid>(
    map_topic, map_qos, std::bind(&RosInterface::onMap, this, std::placeholders::_1));
  map_volatile_sub_ = create_subscription<nav_msgs::msg::OccupancyGrid>(
    map_topic, map_volatile_qos, std::bind(&RosInterface::onMap, this, std::placeholders::_1));
  odom_sub_ = create_subscription<nav_msgs::msg::Odometry>(
    odom_topic, latest_qos, std::bind(&RosInterface::onOdom, this, std::placeholders::_1));

  const std::vector<std::pair<std::string, std::string>> path_topics = {
    {"/plan", "全局路径"},
    {"/global_plan", "全局路径"},
    {"/local_plan", "局部路径"},
    {"/received_global_plan", "控制路径"},
    {"/transformed_global_plan", "控制路径"},
  };
  for (const auto & [topic, label] : path_topics) {
    path_subs_.push_back(create_subscription<nav_msgs::msg::Path>(
      topic, latest_qos,
      [this, label](nav_msgs::msg::Path::SharedPtr msg) { onPath(msg, label); }));
  }

  const std::vector<std::pair<std::string, std::string>> costmap_topics = {
    {"/global_costmap/costmap", "全局膨胀层"},
    {"/global_costmap/costmap_raw", "全局代价层"},
    {"/local_costmap/costmap", "局部膨胀层"},
    {"/local_costmap/costmap_raw", "局部代价层"},
  };
  for (const auto & [topic, label] : costmap_topics) {
    costmap_subs_.push_back(create_subscription<nav_msgs::msg::OccupancyGrid>(
      topic, latest_qos,
      [this, label](nav_msgs::msg::OccupancyGrid::SharedPtr msg) { onCostmap(msg, label); }));
  }

  battery_sub_ = create_subscription<sensor_msgs::msg::BatteryState>(
    "/battery_state", latest_qos, std::bind(&RosInterface::onBattery, this, std::placeholders::_1));
  float32_subs_.push_back(create_subscription<std_msgs::msg::Float32>(
    "/voltage", latest_qos, [this](std_msgs::msg::Float32::SharedPtr msg) { onVoltage(msg->data); }));
  float32_subs_.push_back(create_subscription<std_msgs::msg::Float32>(
    "/battery_voltage", latest_qos,
    [this](std_msgs::msg::Float32::SharedPtr msg) { onVoltage(msg->data); }));
  float32_subs_.push_back(create_subscription<std_msgs::msg::Float32>(
    "/battery_percent", latest_qos,
    [this](std_msgs::msg::Float32::SharedPtr msg) { onPercent(msg->data); }));
}

RosInterface::~RosInterface()
{
  stop();
}

void RosInterface::start()
{
  if (running_.exchange(true)) {
    return;
  }
  executor_.add_node(shared_from_this());
  spin_thread_ = std::thread(&RosInterface::spinLoop, this);
}

void RosInterface::stop()
{
  if (!running_.exchange(false)) {
    return;
  }
  executor_.cancel();
  if (spin_thread_.joinable()) {
    spin_thread_.join();
  }
  executor_.remove_node(shared_from_this());
}

void RosInterface::spinLoop()
{
  while (running_ && rclcpp::ok()) {
    executor_.spin_some(std::chrono::milliseconds(2));
    Pose2D pose;
    for (const auto & frame : {base_frame_, fallback_base_frame_}) {
      if (frame.empty()) {
        continue;
      }
      try {
        const auto tf = tf_buffer_->lookupTransform(fixed_frame_, frame, tf2::TimePointZero);
        pose.x = tf.transform.translation.x;
        pose.y = tf.transform.translation.y;
        pose.yaw = tf2::getYaw(tf.transform.rotation);
        pose.frame_id = fixed_frame_;
        pose.valid = true;
        break;
      } catch (const tf2::TransformException &) {
      }
    }

    if (pose.valid) {
      std::lock_guard<std::mutex> lock(state_mutex_);
      state_.robot_pose = pose;
      stampSeen("tf");
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
}

AppState RosInterface::stateSnapshot() const
{
  std::lock_guard<std::mutex> lock(state_mutex_);
  AppState copy = state_;
  const auto now = this->now();
  copy.ages.map = topicAge("map", now);
  copy.ages.scan = topicAge("scan", now);
  copy.ages.camera = topicAge("camera", now);
  copy.ages.odom = topicAge("odom", now);
  copy.ages.tf = topicAge("tf", now);
  copy.ages.battery = topicAge("battery", now);
  return copy;
}

void RosInterface::publishVelocity(double linear, double angular)
{
  geometry_msgs::msg::Twist msg;
  msg.linear.x = linear;
  msg.angular.z = angular;
  cmd_pub_->publish(msg);
  std::lock_guard<std::mutex> lock(state_mutex_);
  state_.cmd_linear = linear;
  state_.cmd_angular = angular;
}

void RosInterface::publishGoal(double x, double y, double yaw)
{
  geometry_msgs::msg::PoseStamped msg;
  msg.header.stamp = now();
  msg.header.frame_id = fixed_frame_;
  msg.pose.position.x = x;
  msg.pose.position.y = y;
  msg.pose.orientation = yawToQuaternion(yaw);
  goal_pub_->publish(msg);
}

void RosInterface::publishInitialPose(double x, double y, double yaw)
{
  geometry_msgs::msg::PoseWithCovarianceStamped msg;
  msg.header.stamp = now();
  msg.header.frame_id = fixed_frame_;
  msg.pose.pose.position.x = x;
  msg.pose.pose.position.y = y;
  msg.pose.pose.orientation = yawToQuaternion(yaw);
  msg.pose.covariance[0] = 0.25;
  msg.pose.covariance[7] = 0.25;
  msg.pose.covariance[35] = 0.0685;
  initial_pose_pub_->publish(msg);
}

void RosInterface::stampSeen(const std::string & name)
{
  last_seen_[name] = now();
}

double RosInterface::topicAge(const std::string & name, const rclcpp::Time & now_time) const
{
  const auto it = last_seen_.find(name);
  if (it == last_seen_.end()) {
    return -1.0;
  }
  return (now_time - it->second).seconds();
}

Pose2D RosInterface::transformPose(const std::string & frame_id, double x, double y, double yaw) const
{
  Pose2D pose;
  pose.x = x;
  pose.y = y;
  pose.yaw = yaw;
  pose.frame_id = frame_id.empty() ? fixed_frame_ : frame_id;
  pose.valid = true;
  if (pose.frame_id == fixed_frame_) {
    return pose;
  }
  try {
    const auto tf = tf_buffer_->lookupTransform(fixed_frame_, pose.frame_id, tf2::TimePointZero);
    const auto tyaw = tf2::getYaw(tf.transform.rotation);
    const auto c = std::cos(tyaw);
    const auto s = std::sin(tyaw);
    pose.x = tf.transform.translation.x + c * x - s * y;
    pose.y = tf.transform.translation.y + s * x + c * y;
    pose.yaw = normalizeAngle(yaw + tyaw);
    pose.frame_id = fixed_frame_;
  } catch (const tf2::TransformException &) {
  }
  return pose;
}

void RosInterface::onCamera(const sensor_msgs::msg::CompressedImage::SharedPtr msg)
{
  QImage image;
  image.loadFromData(reinterpret_cast<const uchar *>(msg->data.data()), static_cast<int>(msg->data.size()));
  if (image.isNull()) {
    return;
  }
  std::lock_guard<std::mutex> lock(state_mutex_);
  state_.camera_image = image.convertToFormat(QImage::Format_RGB888);
  stampSeen("camera");
}

void RosInterface::onScan(const sensor_msgs::msg::LaserScan::SharedPtr msg)
{
  std::vector<QPointF> points;
  points.reserve(msg->ranges.size());
  const auto max_points = 3600U;
  const auto step = std::max(1U, static_cast<unsigned int>(std::ceil(msg->ranges.size() / static_cast<double>(max_points))));
  for (size_t i = 0; i < msg->ranges.size(); i += step) {
    const float range = msg->ranges[i];
    if (!std::isfinite(range) || range < msg->range_min || range > msg->range_max) {
      continue;
    }
    const double angle = msg->angle_min + static_cast<double>(i) * msg->angle_increment;
    points.emplace_back(range * std::cos(angle), range * std::sin(angle));
  }

  std::lock_guard<std::mutex> lock(state_mutex_);
  state_.scan_points = std::move(points);
  state_.scan_range_max = std::max(1.0f, msg->range_max);
  stampSeen("scan");
}

void RosInterface::onMap(const nav_msgs::msg::OccupancyGrid::SharedPtr msg)
{
  auto layer = occupancyToMapLayer(*msg, false);
  std::lock_guard<std::mutex> lock(state_mutex_);
  state_.map = std::move(layer);
  state_.has_map = true;
  stampSeen("map");
}

void RosInterface::onCostmap(
  const nav_msgs::msg::OccupancyGrid::SharedPtr msg, const std::string & label)
{
  auto layer = occupancyToMapLayer(*msg, true);
  std::lock_guard<std::mutex> lock(state_mutex_);
  state_.costmaps[label] = std::move(layer);
  stampSeen(label);
}

void RosInterface::onPath(const nav_msgs::msg::Path::SharedPtr msg, const std::string & label)
{
  PathLayer layer;
  layer.frame_id = fixed_frame_;
  layer.points.reserve(msg->poses.size());
  const auto source_frame = msg->header.frame_id.empty() ? fixed_frame_ : msg->header.frame_id;
  for (const auto & pose_stamped : msg->poses) {
    const auto pose = transformPose(
      source_frame,
      pose_stamped.pose.position.x,
      pose_stamped.pose.position.y,
      0.0);
    layer.points.emplace_back(pose.x, pose.y);
    layer.frame_id = pose.frame_id;
  }
  std::lock_guard<std::mutex> lock(state_mutex_);
  state_.paths[label] = std::move(layer);
  stampSeen(label);
}

void RosInterface::onOdom(const nav_msgs::msg::Odometry::SharedPtr msg)
{
  const auto yaw = tf2::getYaw(msg->pose.pose.orientation);
  auto pose = transformPose(
    msg->header.frame_id.empty() ? "odom" : msg->header.frame_id,
    msg->pose.pose.position.x,
    msg->pose.pose.position.y,
    yaw);

  std::lock_guard<std::mutex> lock(state_mutex_);
  if (!state_.robot_pose.valid) {
    state_.robot_pose = pose;
  }
  state_.odom_linear = msg->twist.twist.linear.x;
  state_.odom_angular = msg->twist.twist.angular.z;
  stampSeen("odom");
}

void RosInterface::onBattery(const sensor_msgs::msg::BatteryState::SharedPtr msg)
{
  std::lock_guard<std::mutex> lock(state_mutex_);
  if (std::isfinite(msg->voltage)) {
    state_.battery.voltage = msg->voltage;
    state_.battery.has_voltage = true;
  }
  if (std::isfinite(msg->percentage) && msg->percentage >= 0.0) {
    state_.battery.percent = msg->percentage <= 1.0 ? msg->percentage * 100.0 : msg->percentage;
    state_.battery.has_percent = true;
  }
  stampSeen("battery");
}

void RosInterface::onVoltage(double value)
{
  std::lock_guard<std::mutex> lock(state_mutex_);
  state_.battery.voltage = value;
  state_.battery.has_voltage = true;
  stampSeen("battery");
}

void RosInterface::onPercent(double value)
{
  std::lock_guard<std::mutex> lock(state_mutex_);
  state_.battery.percent = value <= 1.0 ? value * 100.0 : value;
  state_.battery.has_percent = true;
  stampSeen("battery");
}

MapLayer RosInterface::occupancyToMapLayer(
  const nav_msgs::msg::OccupancyGrid & msg, bool costmap) const
{
  MapLayer layer;
  layer.width = static_cast<int>(msg.info.width);
  layer.height = static_cast<int>(msg.info.height);
  layer.resolution = msg.info.resolution;
  const auto frame = msg.header.frame_id.empty() ? fixed_frame_ : msg.header.frame_id;
  const auto origin_yaw = tf2::getYaw(msg.info.origin.orientation);
  const auto origin = transformPose(
    frame,
    msg.info.origin.position.x,
    msg.info.origin.position.y,
    origin_yaw);
  layer.origin_x = origin.x;
  layer.origin_y = origin.y;
  layer.origin_yaw = origin.yaw;
  layer.frame_id = origin.frame_id;

  if (layer.width <= 0 || layer.height <= 0 ||
    msg.data.size() != static_cast<size_t>(layer.width * layer.height))
  {
    return layer;
  }

  if (!costmap) {
    QImage image(layer.width, layer.height, QImage::Format_RGB888);
    for (int y = 0; y < layer.height; ++y) {
      auto * scan = image.scanLine(layer.height - 1 - y);
      for (int x = 0; x < layer.width; ++x) {
        const int value = msg.data[y * layer.width + x];
        uchar gray = 190;
        if (value == 0) {
          gray = 245;
        } else if (value > 0 && value < 50) {
          gray = 165;
        } else if (value >= 50) {
          gray = 35;
        }
        scan[x * 3 + 0] = gray;
        scan[x * 3 + 1] = gray;
        scan[x * 3 + 2] = gray;
      }
    }
    layer.image = image;
    return layer;
  }

  QImage image(layer.width, layer.height, QImage::Format_RGBA8888);
  image.fill(Qt::transparent);
  for (int y = 0; y < layer.height; ++y) {
    auto * scan = reinterpret_cast<QRgb *>(image.scanLine(layer.height - 1 - y));
    for (int x = 0; x < layer.width; ++x) {
      const int value = msg.data[y * layer.width + x];
      if (value <= 0) {
        scan[x] = qRgba(0, 0, 0, 0);
      } else if (value < 35) {
        scan[x] = qRgba(80, 210, 130, 70);
      } else if (value < 75) {
        scan[x] = qRgba(255, 190, 55, 105);
      } else {
        scan[x] = qRgba(245, 70, 65, 150);
      }
    }
  }
  layer.image = image;
  return layer;
}

}  // namespace xuegecar_qt_gui
