#include "camsense_lidar/camsense_lidar.hpp"

#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/laser_scan.hpp>

#include <algorithm>
#include <cmath>
#include <limits>
#include <memory>
#include <string>
#include <vector>

namespace
{

constexpr double kPi = 3.14159265358979323846;

double degToRad(const double degree)
{
  return degree * kPi / 180.0;
}

}  // namespace

class CamsenseLidarNode : public rclcpp::Node
{
public:
  CamsenseLidarNode()
  : Node("camsense_lidar_node")
  {
    port_ = declare_parameter<std::string>("port", "/dev/lidar");
    frame_id_ = declare_parameter<std::string>("frame_id", "laser_frame");
    baudrate_ = declare_parameter<int>("baudrate", 115200);
    angle_min_deg_ = declare_parameter<double>("angle_min", -180.0);
    angle_max_deg_ = declare_parameter<double>("angle_max", 180.0);
    angle_increment_deg_ = declare_parameter<double>("angle_increment", 1.0);
    range_min_ = declare_parameter<double>("range_min", 0.05);
    range_max_ = declare_parameter<double>("range_max", 12.0);
    frequency_ = declare_parameter<double>("frequency", 10.0);
    auto_reconnect_ = declare_parameter<bool>("auto_reconnect", true);
    invalid_range_is_inf_ = declare_parameter<bool>("invalid_range_is_inf", false);
    center_base_angle_deg_ = declare_parameter<double>("center_base_angle", 198.5);
    clockwise_ = declare_parameter<bool>("clockwise", true);
    retry_interval_sec_ = declare_parameter<double>("retry_interval_sec", 1.0);

    if (angle_increment_deg_ <= 0.0) {
      RCLCPP_WARN(get_logger(), "angle_increment_deg must be > 0, using 1.0");
      angle_increment_deg_ = 1.0;
    }
    if (angle_max_deg_ <= angle_min_deg_) {
      RCLCPP_WARN(get_logger(), "angle_max_deg must be > angle_min_deg, using [-180, 180]");
      angle_min_deg_ = -180.0;
      angle_max_deg_ = 180.0;
    }
    if (frequency_ <= 0.0) {
      RCLCPP_WARN(get_logger(), "frequency must be > 0, using 10.0");
      frequency_ = 10.0;
    }
    RCLCPP_INFO(
      get_logger(),
      "Camsense lidar config: port=%s baudrate=%d frame_id=%s angle=[%.1f, %.1f] "
      "increment=%.2f range=[%.2f, %.2f] center_base_angle=%.1f clockwise=%s",
      port_.c_str(), baudrate_, frame_id_.c_str(), angle_min_deg_, angle_max_deg_,
      angle_increment_deg_, range_min_, range_max_, center_base_angle_deg_,
      clockwise_ ? "true" : "false");

    lidar_ = std::make_unique<camsense_lidar::CamsenseLidar>(
      port_, baudrate_, static_cast<float>(center_base_angle_deg_));
    scan_pub_ = create_publisher<sensor_msgs::msg::LaserScan>("scan", rclcpp::SensorDataQoS());
    timer_ = create_wall_timer(std::chrono::milliseconds(5), [this]() {pollLidar();});

    tryOpen();
  }

private:
  void tryOpen()
  {
    if (lidar_->isOpen()) {
      return;
    }

    const rclcpp::Time now = this->now();
    if (last_open_attempt_.nanoseconds() != 0 &&
      !auto_reconnect_)
    {
      return;
    }

    if (last_open_attempt_.nanoseconds() != 0 &&
      (now - last_open_attempt_).seconds() < retry_interval_sec_)
    {
      return;
    }
    last_open_attempt_ = now;

    if (lidar_->open()) {
      RCLCPP_INFO(
        get_logger(), "Opened Camsense lidar on %s at %d baud", port_.c_str(),
        baudrate_);
    } else {
      RCLCPP_WARN(
        get_logger(), "Waiting for Camsense lidar on %s at %d baud", port_.c_str(), baudrate_);
    }
  }

  void pollLidar()
  {
    if (!lidar_->isOpen()) {
      tryOpen();
      return;
    }

    std::vector<camsense_lidar::LidarPoint> points;
    if (lidar_->poll(points) && !points.empty()) {
      publishScan(points);
      ++published_scans_;
    } else if (!lidar_->isOpen() && auto_reconnect_) {
      RCLCPP_WARN(get_logger(), "Serial port disconnected, retrying");
    }
    logDriverStats();
  }

  void logDriverStats()
  {
    const rclcpp::Time now = this->now();
    if (last_stats_log_.nanoseconds() != 0 && (now - last_stats_log_).seconds() < 2.0) {
      return;
    }
    last_stats_log_ = now;

    const auto stats = lidar_->getStats();
    RCLCPP_INFO(
      get_logger(),
      "Camsense stats: bytes=%lu packets=%lu bad_headers=%lu valid_points=%lu "
      "invalid_points=%lu completed_scans=%lu published_scans=%lu buffered_points=%zu",
      stats.bytes_read, stats.packets, stats.invalid_headers, stats.valid_points,
      stats.invalid_points, stats.completed_scans, published_scans_, stats.current_scan_points);
  }

  void publishScan(const std::vector<camsense_lidar::LidarPoint> & points)
  {
    sensor_msgs::msg::LaserScan scan;
    scan.header.stamp = this->now();
    scan.header.frame_id = frame_id_;
    scan.angle_min = static_cast<float>(degToRad(angle_min_deg_));
    scan.angle_max = static_cast<float>(degToRad(angle_max_deg_));
    scan.angle_increment = static_cast<float>(degToRad(angle_increment_deg_));
    const double scan_time = 1.0 / frequency_;
    scan.scan_time = static_cast<float>(scan_time);
    scan.range_min = static_cast<float>(range_min_);
    scan.range_max = static_cast<float>(range_max_);

    const auto bin_count = static_cast<std::size_t>(
      std::floor((angle_max_deg_ - angle_min_deg_) / angle_increment_deg_)) + 1U;
    scan.time_increment =
      bin_count > 1 ? static_cast<float>(scan_time / static_cast<double>(bin_count - 1U)) : 0.0F;
    const float invalid_range = invalid_range_is_inf_ ?
      std::numeric_limits<float>::infinity() : 0.0F;
    scan.ranges.assign(bin_count, invalid_range);
    scan.intensities.assign(bin_count, 0.0F);

    for (const auto & point : points) {
      double angle_deg = clockwise_ ? 360.0 - point.angle_deg : point.angle_deg;
      while (angle_deg >= 180.0) {
        angle_deg -= 360.0;
      }
      while (angle_deg < -180.0) {
        angle_deg += 360.0;
      }

      const double range_m = static_cast<double>(point.distance_mm) / 1000.0;
      if (range_m < range_min_ || range_m > range_max_) {
        continue;
      }

      const auto index = static_cast<int>(
        std::lround((angle_deg - angle_min_deg_) / angle_increment_deg_));
      if (index < 0 || static_cast<std::size_t>(index) >= scan.ranges.size()) {
        continue;
      }

      auto & stored_range = scan.ranges[static_cast<std::size_t>(index)];
      if (stored_range == invalid_range || !std::isfinite(stored_range) || range_m < stored_range) {
        stored_range = static_cast<float>(range_m);
      }
    }

    scan_pub_->publish(scan);
  }

  std::string port_;
  int baudrate_ = 115200;
  std::string frame_id_;
  double angle_min_deg_ = -180.0;
  double angle_max_deg_ = 180.0;
  double angle_increment_deg_ = 1.0;
  double range_min_ = 0.05;
  double range_max_ = 12.0;
  double frequency_ = 10.0;
  double center_base_angle_deg_ = 198.5;
  bool clockwise_ = true;
  bool auto_reconnect_ = true;
  bool invalid_range_is_inf_ = false;
  double retry_interval_sec_ = 1.0;

  std::unique_ptr<camsense_lidar::CamsenseLidar> lidar_;
  rclcpp::Publisher<sensor_msgs::msg::LaserScan>::SharedPtr scan_pub_;
  rclcpp::TimerBase::SharedPtr timer_;
  rclcpp::Time last_open_attempt_ {};
  rclcpp::Time last_stats_log_ {};
  uint64_t published_scans_ = 0;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<CamsenseLidarNode>());
  rclcpp::shutdown();
  return 0;
}
