// Keep Nav2's safety processing active while permitting bounded escape.
#include "xuegecar_bringup/safe_collision_monitor.hpp"

#include <algorithm>
#include <cmath>
#include <stdexcept>
#include <string>

#include "nav2_util/robot_utils.hpp"

namespace xuegecar_bringup
{
using nav2_collision_monitor::Point;
using nav2_collision_monitor::Velocity;

void EscapeScan::configure()
{
  Scan::configure();
  auto node = node_.lock();
  data_sub_ = node->create_subscription<sensor_msgs::msg::LaserScan>(
    node->get_parameter(source_name_ + ".topic").as_string(), rclcpp::SensorDataQoS(),
    std::bind(&EscapeScan::receive, this, std::placeholders::_1));
}

void EscapeScan::receive(sensor_msgs::msg::LaserScan::ConstSharedPtr message)
{
  received_ = std::chrono::steady_clock::now();
  dataCallback(message);
}

bool EscapeScan::fresh() const
{
  return received_ != std::chrono::steady_clock::time_point{} &&
         std::chrono::duration<double>(std::chrono::steady_clock::now() - received_).count() <
         source_timeout_.seconds();
}

bool EscapeScan::readRaw(const rclcpp::Time & time, std::vector<Point> & points)
{
  return getEnabled() && fresh() && Scan::getData(time, points);
}

bool EscapeScan::getData(const rclcpp::Time & time, std::vector<Point> & points)
{
  if (!readRaw(time, points)) {
    return false;
  }
  if (ignore) {
    points.erase(std::remove_if(points.begin(), points.end(), ignore), points.end());
  }
  return true;
}

SafeCollisionMonitor::SafeCollisionMonitor(const rclcpp::NodeOptions & options)
: CollisionMonitor(options) {}

nav2_util::CallbackReturn SafeCollisionMonitor::on_configure(
  const rclcpp_lifecycle::State & state)
{
  if (CollisionMonitor::on_configure(state) != nav2_util::CallbackReturn::SUCCESS) {
    return nav2_util::CallbackReturn::FAILURE;
  }
  try {
    // This control stack has one LaserScan source and one rectangular virtual body.
    if (get_parameter("observation_sources").as_string_array() !=
      std::vector<std::string>{"scan"} || polygons_.size() != 2)
    {
      throw std::runtime_error("Expected scan source and VirtualStop/FootprintApproach");
    }
    auto number = [this](const std::string & name, double initial) {
        nav2_util::declare_parameter_if_not_declared(
          shared_from_this(), name, rclcpp::ParameterValue(initial));
        const double value = get_parameter(name).as_double();
        if (!std::isfinite(value) || value <= 0.0) {
          throw std::runtime_error(name + " must be finite and positive");
        }
        return value;
      };
    padding_ = number("hard_stop_padding", 0.02);
    overlap_ = number("escape_body_overlap", 0.005);
    escape_speed_ = number("escape_max_speed", 0.03);
    odom_timeout_ = number("escape_odom_timeout", 0.5);
    command_timeout_ = number("command_timeout", 0.3);
    stopped_linear_ = number("escape_stopped_linear", 0.04);
    stopped_angular_ = number("escape_stopped_angular", 0.05);
    const double timeout = number("source_timeout", 1.0);
    std::vector<Point> vertices;
    polygons_.front()->getPolygon(vertices);
    if (vertices.size() != 4 ||
      get_parameter("polygons").as_string_array() !=
      std::vector<std::string>{"VirtualStop", "FootprintApproach"})
    {
      throw std::runtime_error("VirtualStop must be the configured rectangular hard stop");
    }
    front_ = rear_ = vertices.front().x;
    left_ = right_ = vertices.front().y;
    for (const auto & vertex : vertices) {
      front_ = std::max(front_, vertex.x);
      rear_ = std::min(rear_, vertex.x);
      left_ = std::max(left_, vertex.y);
      right_ = std::min(right_, vertex.y);
    }
    min_points_ = polygons_.front()->getMinPoints();
    escape_scan_ = std::make_shared<EscapeScan>(
      shared_from_this(), "scan", tf_buffer_, get_parameter("base_frame_id").as_string(),
      get_parameter("odom_frame_id").as_string(),
      tf2::durationFromSec(get_parameter("transform_tolerance").as_double()),
      rclcpp::Duration::from_seconds(timeout), get_parameter("base_shift_correction").as_bool());
    escape_scan_->configure();
    sources_ = {escape_scan_};
    nav2_util::declare_parameter_if_not_declared(
      shared_from_this(), "odom_topic", rclcpp::ParameterValue("/odometry/filtered"));
    odom_subscription_ = create_subscription<nav_msgs::msg::Odometry>(
      get_parameter("odom_topic").as_string(), rclcpp::QoS(10),
      std::bind(&SafeCollisionMonitor::receiveOdometry, this, std::placeholders::_1));
    cmd_vel_in_sub_ = std::make_unique<nav2_util::TwistSubscriber>(
      shared_from_this(), get_parameter("cmd_vel_in_topic").as_string(), 1,
      [this](geometry_msgs::msg::Twist::SharedPtr message) {
        auto stamped = std::make_shared<geometry_msgs::msg::TwistStamped>();
        stamped->twist = *message;
        receiveCommand(stamped);
      }, std::bind(&SafeCollisionMonitor::receiveCommand, this, std::placeholders::_1));
    watchdog_timer_ = create_wall_timer(
      std::chrono::milliseconds(50), std::bind(&SafeCollisionMonitor::watchdog, this));
    return nav2_util::CallbackReturn::SUCCESS;
  } catch (const std::exception & error) {
    RCLCPP_ERROR(get_logger(), "Escape configuration failed: %s", error.what());
    on_cleanup(state);
    return nav2_util::CallbackReturn::FAILURE;
  }
}

nav2_util::CallbackReturn SafeCollisionMonitor::on_cleanup(
  const rclcpp_lifecycle::State & state)
{
  watchdog_timer_.reset();
  odom_subscription_.reset();
  odometry_.reset();
  escape_scan_.reset();
  escape_side_ = 0;
  command_received_ = {};
  odom_received_ = {};
  return CollisionMonitor::on_cleanup(state);
}

void SafeCollisionMonitor::receiveOdometry(nav_msgs::msg::Odometry::ConstSharedPtr message)
{
  odometry_ = message;
  odom_received_ = std::chrono::steady_clock::now();
}

bool SafeCollisionMonitor::stopped() const
{
  if (!odometry_ ||
    std::chrono::duration<double>(std::chrono::steady_clock::now() - odom_received_).count() >=
    odom_timeout_)
  {
    return false;
  }
  const auto & twist = odometry_->twist.twist;
  return std::isfinite(twist.linear.x) && std::isfinite(twist.linear.y) &&
         std::isfinite(twist.angular.z) &&
         std::hypot(twist.linear.x, twist.linear.y) <= stopped_linear_ &&
         std::abs(twist.angular.z) <= stopped_angular_;
}

bool SafeCollisionMonitor::inside(const Point & point) const
{
  return point.x >= rear_ && point.x <= front_ &&
         point.y >= right_ && point.y <= left_;
}

void SafeCollisionMonitor::setEscape(int side)
{
  if (escape_side_ != side) {
    RCLCPP_INFO(
      get_logger(), "Low-speed escape: %s", side > 0 ? "reverse" : side < 0 ? "forward" : "off");
  }
  escape_side_ = side;
}

void SafeCollisionMonitor::receiveCommand(geometry_msgs::msg::TwistStamped::SharedPtr message)
{
  if (!nav2_util::validateTwist(*message)) {
    return;
  }
  const auto & twist = message->twist;
  processSafe({twist.linear.x, twist.linear.y, twist.angular.z}, message->header);
}

void SafeCollisionMonitor::processSafe(
  const Velocity & requested,
  const std_msgs::msg::Header & header)
{
  if (!process_active_) {
    return;
  }
  command_received_ = std::chrono::steady_clock::now();
  escape_scan_->ignore = {};
  Velocity velocity = requested;
  int side = 0;
  std::vector<Point> points;
  if (requested.x != 0.0 && requested.y == 0.0 && requested.tw == 0.0 && stopped() &&
    escape_scan_->readRaw(now(), points))
  {
    int front_points = 0, rear_points = 0, other_points = 0;
    for (const auto & point : points) {
      if (!inside(point)) {
        continue;
      }
      if (point.x >= front_ - padding_ - overlap_) {
        ++front_points;
      } else if (point.x <= rear_ + padding_ + overlap_) {
        ++rear_points;
      } else {
        ++other_points;
      }
    }
    if (other_points == 0 && requested.x < 0.0 &&
      front_points >= min_points_ && rear_points == 0)
    {
      side = 1;
    } else if (other_points == 0 && requested.x > 0.0 &&
      rear_points >= min_points_ && front_points == 0)
    {
      side = -1;
    }
    if (side != 0) {
      velocity.x = std::clamp(requested.x, -escape_speed_, escape_speed_);
      // Only already overlapping, receding points are excluded from BOTH stop
      // and TTC. All approaching points still run through Nav2's real pipeline.
      escape_scan_->ignore = [this, side](const Point & point) {
          return inside(point) && (side > 0 ? point.x >= front_ - padding_ - overlap_ :
                 point.x <= rear_ + padding_ + overlap_);
        };
    }
  }
  setEscape(side);
  process(velocity, header);
  escape_scan_->ignore = {};
}

void SafeCollisionMonitor::watchdog()
{
  if (process_active_ && command_received_ != std::chrono::steady_clock::time_point{} &&
    (std::chrono::duration<double>(std::chrono::steady_clock::now() - command_received_).count() >=
    command_timeout_ || !escape_scan_->fresh() || (escape_side_ != 0 && !stopped())))
  {
    escape_scan_->ignore = {};
    setEscape(0);
    process({0.0, 0.0, 0.0}, std_msgs::msg::Header());
  }
}
}  // namespace xuegecar_bringup
