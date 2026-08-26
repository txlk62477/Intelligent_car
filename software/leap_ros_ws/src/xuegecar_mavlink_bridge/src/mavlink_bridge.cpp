#include <geometry_msgs/msg/twist.hpp>
#include <nav_msgs/msg/odometry.hpp>
#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/imu.hpp>
#include <sensor_msgs/msg/laser_scan.hpp>

#include "my_robot/mavlink.h"

#include <algorithm>
#include <array>
#include <atomic>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <fcntl.h>
#include <limits>
#include <mutex>
#include <string>
#include <termios.h>
#include <thread>
#include <unistd.h>
#include <vector>

namespace
{
constexpr double kGravity = 9.80665;
constexpr double kPi = 3.14159265358979323846;
constexpr double kDegToRad = kPi / 180.0;
constexpr double kRadToDeg = 180.0 / kPi;
constexpr std::size_t kLidarPacketBins = MAVLINK_MSG_OBSTACLE_DISTANCE_FIELD_DISTANCES_LEN;
constexpr std::size_t kLidarPacketCount = 5;
constexpr std::size_t kLidarScanBins = kLidarPacketBins * kLidarPacketCount;
constexpr double kLidarScanIncrementDeg = 1.0;

std::string normalize_port(const std::string & port)
{
  if (port.empty()) {
    return "/dev/ttyUSB0";
  }
  if (port.front() == '/') {
    return port;
  }
  return "/dev/" + port;
}

bool baud_to_speed(const int baudrate, speed_t & speed)
{
  switch (baudrate) {
    case 9600:
      speed = B9600;
      return true;
    case 19200:
      speed = B19200;
      return true;
    case 38400:
      speed = B38400;
      return true;
    case 57600:
      speed = B57600;
      return true;
    case 115200:
      speed = B115200;
      return true;
#ifdef B230400
    case 230400:
      speed = B230400;
      return true;
#endif
#ifdef B460800
    case 460800:
      speed = B460800;
      return true;
#endif
#ifdef B500000
    case 500000:
      speed = B500000;
      return true;
#endif
#ifdef B921600
    case 921600:
      speed = B921600;
      return true;
#endif
#ifdef B1000000
    case 1000000:
      speed = B1000000;
      return true;
#endif
    default:
      return false;
  }
}

double normalize_degrees_180(double degrees)
{
  while (degrees >= 180.0) {
    degrees -= 360.0;
  }
  while (degrees < -180.0) {
    degrees += 360.0;
  }
  return degrees;
}

double normalize_degrees_360(double degrees)
{
  degrees = std::fmod(degrees, 360.0);
  if (degrees < 0.0) {
    degrees += 360.0;
  }
  return degrees;
}

geometry_msgs::msg::Quaternion quaternion_from_rpy(
  const double roll, const double pitch, const double yaw)
{
  const double cy = std::cos(yaw * 0.5);
  const double sy = std::sin(yaw * 0.5);
  const double cp = std::cos(pitch * 0.5);
  const double sp = std::sin(pitch * 0.5);
  const double cr = std::cos(roll * 0.5);
  const double sr = std::sin(roll * 0.5);

  geometry_msgs::msg::Quaternion q;
  q.w = cr * cp * cy + sr * sp * sy;
  q.x = sr * cp * cy - cr * sp * sy;
  q.y = cr * sp * cy + sr * cp * sy;
  q.z = cr * cp * sy - sr * sp * cy;
  return q;
}

geometry_msgs::msg::Quaternion quaternion_from_mavlink_wxyz(const float q[4])
{
  geometry_msgs::msg::Quaternion out;
  out.w = q[0];
  out.x = q[1];
  out.y = q[2];
  out.z = q[3];

  const double norm = std::sqrt(
    out.w * out.w + out.x * out.x + out.y * out.y + out.z * out.z);
  if (norm > 1.0e-9) {
    out.w /= norm;
    out.x /= norm;
    out.y /= norm;
    out.z /= norm;
  } else {
    out.w = 1.0;
    out.x = 0.0;
    out.y = 0.0;
    out.z = 0.0;
  }
  return out;
}

double range_from_cm(const uint16_t range_cm, const uint16_t max_distance_cm)
{
  if (range_cm == UINT16_MAX || range_cm == static_cast<uint16_t>(max_distance_cm + 1U)) {
    return std::numeric_limits<float>::infinity();
  }
  return static_cast<double>(range_cm) * 0.01;
}

void expand_upper_triangle_covariance(
  const float upper[21],
  std::array<double, 36> & out,
  const std::array<double, 6> & signs = {1.0, 1.0, 1.0, 1.0, 1.0, 1.0})
{
  out.fill(0.0);
  if (std::isnan(upper[0])) {
    return;
  }

  std::size_t index = 0;
  for (std::size_t row = 0; row < 6; ++row) {
    for (std::size_t col = row; col < 6; ++col) {
      const double value = static_cast<double>(upper[index++]) * signs[row] * signs[col];
      out[row * 6 + col] = value;
      out[col * 6 + row] = value;
    }
  }
}
}  // namespace

class MavlinkBridge : public rclcpp::Node
{
public:
  MavlinkBridge()
  : Node("mavlink_bridge")
  {
    port_ = declare_parameter<std::string>("port", "ttyUSB0");
    baudrate_ = declare_parameter<int>("baudrate", 230400);

    imu_topic_ = declare_parameter<std::string>("imu_topic", "/imu");
    odom_topic_ = declare_parameter<std::string>("odom_topic", "/odom");
    scan_topic_ = declare_parameter<std::string>("scan_topic", "/scan");
    cmd_vel_topic_ = declare_parameter<std::string>("cmd_vel_topic", "/cmd_vel");

    odom_frame_id_ = declare_parameter<std::string>("odom_frame_id", "odom");
    base_frame_id_ = declare_parameter<std::string>("base_frame_id", "base_link");
    imu_frame_id_ = declare_parameter<std::string>("imu_frame_id", "imu_link");
    scan_frame_id_ = declare_parameter<std::string>("scan_frame_id", "laser_frame");

    reconnect_interval_ms_ = declare_parameter<int>("reconnect_interval_ms", 1000);
    scan_time_ = declare_parameter<double>("scan_time", 0.1);
    convert_odom_twist_frd_to_flu_ =
      declare_parameter<bool>("convert_odom_twist_frd_to_flu", true);
    convert_imu_frd_to_flu_ = declare_parameter<bool>("convert_imu_frd_to_flu", true);
    reorder_scan_to_ros_angles_ = declare_parameter<bool>("reorder_scan_to_ros_angles", true);
    convert_cmd_vel_flu_to_frd_ = declare_parameter<bool>("convert_cmd_vel_flu_to_frd", true);

    const int source_system_id = declare_parameter<int>("source_system_id", 255);
    const int source_component_id = declare_parameter<int>("source_component_id", 190);
    const int target_system_id = declare_parameter<int>("target_system_id", 1);
    const int target_component_id = declare_parameter<int>("target_component_id", 1);
    source_system_id_ = static_cast<uint8_t>(std::clamp(source_system_id, 0, 255));
    source_component_id_ = static_cast<uint8_t>(std::clamp(source_component_id, 0, 255));
    target_system_id_ = static_cast<uint8_t>(std::clamp(target_system_id, 0, 255));
    target_component_id_ = static_cast<uint8_t>(std::clamp(target_component_id, 0, 255));

    imu_pub_ = create_publisher<sensor_msgs::msg::Imu>(imu_topic_, rclcpp::SensorDataQoS());
    odom_pub_ = create_publisher<nav_msgs::msg::Odometry>(odom_topic_, rclcpp::SensorDataQoS());
    scan_pub_ = create_publisher<sensor_msgs::msg::LaserScan>(scan_topic_, rclcpp::SensorDataQoS());
    cmd_vel_sub_ = create_subscription<geometry_msgs::msg::Twist>(
      cmd_vel_topic_, rclcpp::SensorDataQoS(),
      [this](const geometry_msgs::msg::Twist::SharedPtr msg) {
        handle_cmd_vel(*msg);
      });

    reset_imu_state();
    reset_lidar_scan_accumulator();

    running_.store(true);
    reader_thread_ = std::thread(&MavlinkBridge::read_loop, this);

    RCLCPP_INFO(
      get_logger(),
      "MAVLink bridge configured: port=%s baudrate=%d cmd_vel=%s imu=%s odom=%s scan=%s",
      normalize_port(port_).c_str(), baudrate_, cmd_vel_topic_.c_str(), imu_topic_.c_str(),
      odom_topic_.c_str(), scan_topic_.c_str());
  }

  ~MavlinkBridge() override
  {
    running_.store(false);
    if (reader_thread_.joinable()) {
      reader_thread_.join();
    }
    close_serial();
  }

private:
  void reset_imu_state()
  {
    imu_msg_ = sensor_msgs::msg::Imu();
    imu_msg_.orientation.w = 1.0;
    imu_msg_.orientation_covariance[0] = -1.0;
    imu_msg_.angular_velocity_covariance[0] = -1.0;
    imu_msg_.linear_acceleration_covariance[0] = -1.0;
    has_orientation_ = false;
    has_angular_velocity_ = false;
    has_linear_acceleration_ = false;
  }

  bool open_serial()
  {
    std::lock_guard<std::mutex> lock(serial_mutex_);
    close_serial_locked();

    const std::string path = normalize_port(port_);
    fd_ = ::open(path.c_str(), O_RDWR | O_NOCTTY);
    if (fd_ < 0) {
      RCLCPP_WARN_THROTTLE(
        get_logger(), *get_clock(), 5000, "Failed to open %s: %s", path.c_str(),
        std::strerror(errno));
      return false;
    }

    termios options {};
    if (tcgetattr(fd_, &options) != 0) {
      RCLCPP_ERROR(get_logger(), "tcgetattr(%s) failed: %s", path.c_str(), std::strerror(errno));
      close_serial_locked();
      return false;
    }

    cfmakeraw(&options);
    options.c_cflag |= static_cast<tcflag_t>(CLOCAL | CREAD);
    options.c_cflag &= static_cast<tcflag_t>(~CSIZE);
    options.c_cflag |= CS8;
    options.c_cflag &= static_cast<tcflag_t>(~PARENB);
    options.c_cflag &= static_cast<tcflag_t>(~CSTOPB);
#ifdef CRTSCTS
    options.c_cflag &= static_cast<tcflag_t>(~CRTSCTS);
#endif
    options.c_iflag &= static_cast<tcflag_t>(~(IXON | IXOFF | IXANY));
    options.c_cc[VMIN] = 0;
    options.c_cc[VTIME] = 1;

    speed_t speed {};
    if (!baud_to_speed(baudrate_, speed)) {
      RCLCPP_ERROR(get_logger(), "Unsupported baudrate: %d", baudrate_);
      close_serial_locked();
      return false;
    }
    cfsetispeed(&options, speed);
    cfsetospeed(&options, speed);

    if (tcsetattr(fd_, TCSANOW, &options) != 0) {
      RCLCPP_ERROR(get_logger(), "tcsetattr(%s) failed: %s", path.c_str(), std::strerror(errno));
      close_serial_locked();
      return false;
    }

    tcflush(fd_, TCIFLUSH);
    RCLCPP_INFO(get_logger(), "Opened MAVLink UART %s at %d baud", path.c_str(), baudrate_);
    return true;
  }

  void close_serial()
  {
    std::lock_guard<std::mutex> lock(serial_mutex_);
    close_serial_locked();
  }

  void close_serial_locked()
  {
    if (fd_ >= 0) {
      ::close(fd_);
      fd_ = -1;
    }
  }

  void sleep_reconnect_interval() const
  {
    const auto total = std::chrono::milliseconds(std::max(100, reconnect_interval_ms_));
    auto waited = std::chrono::milliseconds(0);
    while (running_.load() && waited < total) {
      constexpr auto step = std::chrono::milliseconds(100);
      std::this_thread::sleep_for(step);
      waited += step;
    }
  }

  int serial_fd() const
  {
    std::lock_guard<std::mutex> lock(serial_mutex_);
    return fd_;
  }

  void read_loop()
  {
    mavlink_message_t message {};
    mavlink_status_t status {};
    std::array<uint8_t, 512> buffer {};

    while (running_.load() && rclcpp::ok()) {
      if (serial_fd() < 0 && !open_serial()) {
        sleep_reconnect_interval();
        continue;
      }

      const int fd = serial_fd();
      if (fd < 0) {
        continue;
      }

      const ssize_t bytes_read = ::read(fd, buffer.data(), buffer.size());
      if (bytes_read > 0) {
        for (ssize_t i = 0; i < bytes_read; ++i) {
          if (mavlink_parse_char(MAVLINK_COMM_0, buffer[static_cast<std::size_t>(i)], &message, &status)) {
            handle_mavlink_message(message);
          }
        }
      } else if (bytes_read < 0) {
        if (errno == EINTR) {
          continue;
        }
        RCLCPP_WARN(
          get_logger(), "Serial read failed, reconnecting %s: %s",
          normalize_port(port_).c_str(), std::strerror(errno));
        close_serial();
        sleep_reconnect_interval();
      }
    }
  }

  uint32_t mavlink_time_boot_ms() const
  {
    const auto elapsed = std::chrono::steady_clock::now() - start_time_;
    return static_cast<uint32_t>(
      std::chrono::duration_cast<std::chrono::milliseconds>(elapsed).count());
  }

  bool write_serial(const uint8_t * data, const std::size_t length)
  {
    std::lock_guard<std::mutex> lock(serial_mutex_);
    if (fd_ < 0) {
      RCLCPP_WARN_THROTTLE(
        get_logger(), *get_clock(), 2000, "Cannot send MAVLink command: serial port is not open");
      return false;
    }

    std::size_t written = 0;
    while (written < length) {
      const ssize_t ret = ::write(fd_, data + written, length - written);
      if (ret > 0) {
        written += static_cast<std::size_t>(ret);
        continue;
      }
      if (ret < 0 && errno == EINTR) {
        continue;
      }
      RCLCPP_WARN_THROTTLE(
        get_logger(), *get_clock(), 2000, "Failed to send MAVLink command on %s: %s",
        normalize_port(port_).c_str(), std::strerror(errno));
      return false;
    }

    return true;
  }

  bool send_mavlink_message(const mavlink_message_t & message)
  {
    std::array<uint8_t, MAVLINK_MAX_PACKET_LEN> buffer {};
    const uint16_t length = mavlink_msg_to_send_buffer(buffer.data(), &message);
    return write_serial(buffer.data(), length);
  }

  void handle_cmd_vel(const geometry_msgs::msg::Twist & cmd_vel)
  {
    const float vx = static_cast<float>(cmd_vel.linear.x);
    const float vy =
      static_cast<float>(convert_cmd_vel_flu_to_frd_ ? -cmd_vel.linear.y : cmd_vel.linear.y);
    const float yaw_rate = static_cast<float>(cmd_vel.angular.z);

    constexpr uint16_t kVelocityTypeMask =
      POSITION_TARGET_TYPEMASK_X_IGNORE |
      POSITION_TARGET_TYPEMASK_Y_IGNORE |
      POSITION_TARGET_TYPEMASK_Z_IGNORE |
      POSITION_TARGET_TYPEMASK_VZ_IGNORE |
      POSITION_TARGET_TYPEMASK_AX_IGNORE |
      POSITION_TARGET_TYPEMASK_AY_IGNORE |
      POSITION_TARGET_TYPEMASK_AZ_IGNORE |
      POSITION_TARGET_TYPEMASK_YAW_IGNORE;

    mavlink_message_t message {};
    mavlink_msg_set_position_target_local_ned_pack(
      source_system_id_, source_component_id_, &message,
      mavlink_time_boot_ms(),
      target_system_id_, target_component_id_,
      MAV_FRAME_BODY_NED,
      kVelocityTypeMask,
      0.0F, 0.0F, 0.0F,
      vx, vy, 0.0F,
      0.0F, 0.0F, 0.0F,
      0.0F, yaw_rate);

    send_mavlink_message(message);
  }

  std::array<double, 3> convert_body_vector(
    const double x, const double y, const double z, const bool convert) const
  {
    if (!convert) {
      return {x, y, z};
    }
    return {x, -y, -z};
  }

  rclcpp::Time current_stamp() const
  {
    return now();
  }

  void handle_mavlink_message(const mavlink_message_t & message)
  {
    switch (message.msgid) {
      case MAVLINK_MSG_ID_ATTITUDE:
        handle_attitude(message);
        break;
      case MAVLINK_MSG_ID_ATTITUDE_QUATERNION:
        handle_attitude_quaternion(message);
        break;
      case MAVLINK_MSG_ID_SCALED_IMU:
        handle_scaled_imu(message);
        break;
      case MAVLINK_MSG_ID_HIGHRES_IMU:
        handle_highres_imu(message);
        break;
      case MAVLINK_MSG_ID_ODOMETRY:
        handle_odometry(message);
        break;
      case MAVLINK_MSG_ID_OBSTACLE_DISTANCE:
        handle_obstacle_distance(message);
        break;
      default:
        break;
    }
  }

  void handle_attitude(const mavlink_message_t & message)
  {
    mavlink_attitude_t attitude {};
    mavlink_msg_attitude_decode(&message, &attitude);

    imu_msg_.orientation = quaternion_from_rpy(attitude.roll, attitude.pitch, attitude.yaw);
    const auto angular = convert_body_vector(
      attitude.rollspeed, attitude.pitchspeed, attitude.yawspeed, convert_imu_frd_to_flu_);
    imu_msg_.angular_velocity.x = angular[0];
    imu_msg_.angular_velocity.y = angular[1];
    imu_msg_.angular_velocity.z = angular[2];

    has_orientation_ = true;
    has_angular_velocity_ = true;
    publish_imu(current_stamp());
  }

  void handle_attitude_quaternion(const mavlink_message_t & message)
  {
    mavlink_attitude_quaternion_t attitude {};
    mavlink_msg_attitude_quaternion_decode(&message, &attitude);

    const float q[4] = {attitude.q1, attitude.q2, attitude.q3, attitude.q4};
    imu_msg_.orientation = quaternion_from_mavlink_wxyz(q);
    const auto angular = convert_body_vector(
      attitude.rollspeed, attitude.pitchspeed, attitude.yawspeed, convert_imu_frd_to_flu_);
    imu_msg_.angular_velocity.x = angular[0];
    imu_msg_.angular_velocity.y = angular[1];
    imu_msg_.angular_velocity.z = angular[2];

    has_orientation_ = true;
    has_angular_velocity_ = true;
    publish_imu(current_stamp());
  }

  void handle_scaled_imu(const mavlink_message_t & message)
  {
    mavlink_scaled_imu_t imu {};
    mavlink_msg_scaled_imu_decode(&message, &imu);

    const auto acceleration = convert_body_vector(
      static_cast<double>(imu.xacc) * kGravity / 1000.0,
      static_cast<double>(imu.yacc) * kGravity / 1000.0,
      static_cast<double>(imu.zacc) * kGravity / 1000.0,
      convert_imu_frd_to_flu_);
    const auto angular = convert_body_vector(
      static_cast<double>(imu.xgyro) / 1000.0,
      static_cast<double>(imu.ygyro) / 1000.0,
      static_cast<double>(imu.zgyro) / 1000.0,
      convert_imu_frd_to_flu_);

    imu_msg_.linear_acceleration.x = acceleration[0];
    imu_msg_.linear_acceleration.y = acceleration[1];
    imu_msg_.linear_acceleration.z = acceleration[2];
    imu_msg_.angular_velocity.x = angular[0];
    imu_msg_.angular_velocity.y = angular[1];
    imu_msg_.angular_velocity.z = angular[2];

    has_linear_acceleration_ = true;
    has_angular_velocity_ = true;
    publish_imu(current_stamp());
  }

  void handle_highres_imu(const mavlink_message_t & message)
  {
    mavlink_highres_imu_t imu {};
    mavlink_msg_highres_imu_decode(&message, &imu);

    const auto acceleration = convert_body_vector(imu.xacc, imu.yacc, imu.zacc, convert_imu_frd_to_flu_);
    const auto angular = convert_body_vector(imu.xgyro, imu.ygyro, imu.zgyro, convert_imu_frd_to_flu_);

    imu_msg_.linear_acceleration.x = acceleration[0];
    imu_msg_.linear_acceleration.y = acceleration[1];
    imu_msg_.linear_acceleration.z = acceleration[2];
    imu_msg_.angular_velocity.x = angular[0];
    imu_msg_.angular_velocity.y = angular[1];
    imu_msg_.angular_velocity.z = angular[2];

    has_linear_acceleration_ = true;
    has_angular_velocity_ = true;
    publish_imu(current_stamp());
  }

  void publish_imu(const rclcpp::Time & stamp)
  {
    auto msg = imu_msg_;
    msg.header.stamp = stamp;
    msg.header.frame_id = imu_frame_id_;

    msg.orientation_covariance[0] = has_orientation_ ? 0.0 : -1.0;
    msg.angular_velocity_covariance[0] = has_angular_velocity_ ? 0.0 : -1.0;
    msg.linear_acceleration_covariance[0] = has_linear_acceleration_ ? 0.0 : -1.0;

    imu_pub_->publish(msg);
  }

  void handle_odometry(const mavlink_message_t & message)
  {
    mavlink_odometry_t odom {};
    mavlink_msg_odometry_decode(&message, &odom);

    nav_msgs::msg::Odometry ros_odom;
    ros_odom.header.stamp = current_stamp();
    ros_odom.header.frame_id = odom_frame_id_;
    ros_odom.child_frame_id = base_frame_id_;

    ros_odom.pose.pose.position.x = odom.x;
    ros_odom.pose.pose.position.y = odom.y;
    ros_odom.pose.pose.position.z = odom.z;
    ros_odom.pose.pose.orientation = quaternion_from_mavlink_wxyz(odom.q);

    const auto linear = convert_body_vector(odom.vx, odom.vy, odom.vz, convert_odom_twist_frd_to_flu_);
    const auto angular = convert_body_vector(
      odom.rollspeed, odom.pitchspeed, odom.yawspeed, convert_odom_twist_frd_to_flu_);
    ros_odom.twist.twist.linear.x = linear[0];
    ros_odom.twist.twist.linear.y = linear[1];
    ros_odom.twist.twist.linear.z = linear[2];
    ros_odom.twist.twist.angular.x = angular[0];
    ros_odom.twist.twist.angular.y = angular[1];
    ros_odom.twist.twist.angular.z = angular[2];

    std::array<double, 36> pose_covariance {};
    expand_upper_triangle_covariance(odom.pose_covariance, pose_covariance);
    std::copy(
      pose_covariance.begin(), pose_covariance.end(), ros_odom.pose.covariance.begin());

    std::array<double, 36> twist_covariance {};
    const std::array<double, 6> twist_signs =
      convert_odom_twist_frd_to_flu_ ? std::array<double, 6>{1.0, -1.0, -1.0, 1.0, -1.0, -1.0} :
      std::array<double, 6>{1.0, 1.0, 1.0, 1.0, 1.0, 1.0};
    expand_upper_triangle_covariance(odom.velocity_covariance, twist_covariance, twist_signs);
    std::copy(
      twist_covariance.begin(), twist_covariance.end(), ros_odom.twist.covariance.begin());

    odom_pub_->publish(ros_odom);
  }

  void handle_obstacle_distance(const mavlink_message_t & message)
  {
    mavlink_obstacle_distance_t obstacle {};
    mavlink_msg_obstacle_distance_decode(&message, &obstacle);

    const double signed_increment_deg =
      std::abs(obstacle.increment_f) > 1.0e-6 ? static_cast<double>(obstacle.increment_f) :
      static_cast<double>(obstacle.increment == 0 ? kLidarScanIncrementDeg : obstacle.increment);
    const double increment_deg = std::abs(signed_increment_deg);

    if (std::abs(increment_deg - kLidarScanIncrementDeg) > 1.0e-3) {
      RCLCPP_WARN_THROTTLE(
        get_logger(), *get_clock(), 5000,
        "Ignoring OBSTACLE_DISTANCE packet with unsupported increment %.3f deg", increment_deg);
      return;
    }

    const auto start_bin = static_cast<int>(
      std::llround(normalize_degrees_360(obstacle.angle_offset) / kLidarScanIncrementDeg));
    if (
      start_bin < 0 || start_bin >= static_cast<int>(kLidarScanBins) ||
      start_bin % static_cast<int>(kLidarPacketBins) != 0)
    {
      RCLCPP_WARN_THROTTLE(
        get_logger(), *get_clock(), 5000,
        "Ignoring OBSTACLE_DISTANCE packet with unexpected angle_offset %.3f deg",
        static_cast<double>(obstacle.angle_offset));
      return;
    }

    const auto fragment_index = static_cast<std::size_t>(start_bin) / kLidarPacketBins;
    if (fragment_index == 0 && scan_fragment_count_ > 0) {
      reset_lidar_scan_accumulator();
    }

    if (scan_fragment_count_ == 0) {
      scan_min_distance_cm_ = obstacle.min_distance;
      scan_max_distance_cm_ = obstacle.max_distance;
    } else {
      scan_min_distance_cm_ = std::min(scan_min_distance_cm_, obstacle.min_distance);
      scan_max_distance_cm_ = std::max(scan_max_distance_cm_, obstacle.max_distance);
    }

    for (std::size_t src = 0; src < kLidarPacketBins; ++src) {
      const double mav_clockwise_deg =
        static_cast<double>(obstacle.angle_offset) + signed_increment_deg * static_cast<double>(src);
      std::size_t dst = 0;
      if (reorder_scan_to_ros_angles_) {
        const double ros_deg = normalize_degrees_180(-mav_clockwise_deg);
        auto ros_index = static_cast<int>(
          std::llround((ros_deg + 180.0) / kLidarScanIncrementDeg));
        if (ros_index >= static_cast<int>(kLidarScanBins)) {
          ros_index -= static_cast<int>(kLidarScanBins);
        }
        if (ros_index < 0) {
          ros_index += static_cast<int>(kLidarScanBins);
        }
        dst = static_cast<std::size_t>(ros_index);
      } else {
        dst = static_cast<std::size_t>(
          std::llround(normalize_degrees_360(mav_clockwise_deg) / kLidarScanIncrementDeg));
        if (dst >= kLidarScanBins) {
          dst -= kLidarScanBins;
        }
      }

      scan_ranges_[dst] =
        static_cast<float>(range_from_cm(obstacle.distances[src], obstacle.max_distance));
    }

    if (!scan_fragments_[fragment_index]) {
      scan_fragments_[fragment_index] = true;
      ++scan_fragment_count_;
    }

    if (scan_fragment_count_ == kLidarPacketCount) {
      publish_lidar_scan(current_stamp());
      reset_lidar_scan_accumulator();
    }
  }

  void reset_lidar_scan_accumulator()
  {
    scan_ranges_.fill(std::numeric_limits<float>::infinity());
    scan_fragments_.fill(false);
    scan_fragment_count_ = 0;
    scan_min_distance_cm_ = 2;
    scan_max_distance_cm_ = 1200;
  }

  void publish_lidar_scan(const rclcpp::Time & stamp)
  {
    sensor_msgs::msg::LaserScan scan;
    scan.header.stamp = stamp;
    scan.header.frame_id = scan_frame_id_;
    scan.range_min = static_cast<float>(scan_min_distance_cm_ * 0.01);
    scan.range_max = static_cast<float>(scan_max_distance_cm_ * 0.01);
    scan.scan_time = static_cast<float>(scan_time_);
    scan.time_increment = static_cast<float>(scan_time_ / static_cast<double>(kLidarScanBins));
    scan.ranges.assign(scan_ranges_.begin(), scan_ranges_.end());

    if (reorder_scan_to_ros_angles_) {
      scan.angle_min = static_cast<float>(-kPi);
      scan.angle_increment = static_cast<float>(kLidarScanIncrementDeg * kDegToRad);
    } else {
      scan.angle_min = 0.0F;
      scan.angle_increment = static_cast<float>(-kLidarScanIncrementDeg * kDegToRad);
    }
    scan.angle_max =
      scan.angle_min +
      static_cast<float>((static_cast<double>(kLidarScanBins) - 1.0) * scan.angle_increment);

    scan_pub_->publish(scan);
  }

  std::string port_;
  int baudrate_ = 230400;
  std::string imu_topic_;
  std::string odom_topic_;
  std::string scan_topic_;
  std::string cmd_vel_topic_;
  std::string odom_frame_id_;
  std::string base_frame_id_;
  std::string imu_frame_id_;
  std::string scan_frame_id_;
  int reconnect_interval_ms_ = 1000;
  double scan_time_ = 0.1;
  bool convert_odom_twist_frd_to_flu_ = true;
  bool convert_imu_frd_to_flu_ = true;
  bool reorder_scan_to_ros_angles_ = true;
  bool convert_cmd_vel_flu_to_frd_ = true;
  uint8_t source_system_id_ = 255;
  uint8_t source_component_id_ = 190;
  uint8_t target_system_id_ = 1;
  uint8_t target_component_id_ = 1;

  rclcpp::Publisher<sensor_msgs::msg::Imu>::SharedPtr imu_pub_;
  rclcpp::Publisher<nav_msgs::msg::Odometry>::SharedPtr odom_pub_;
  rclcpp::Publisher<sensor_msgs::msg::LaserScan>::SharedPtr scan_pub_;
  rclcpp::Subscription<geometry_msgs::msg::Twist>::SharedPtr cmd_vel_sub_;

  sensor_msgs::msg::Imu imu_msg_;
  bool has_orientation_ = false;
  bool has_angular_velocity_ = false;
  bool has_linear_acceleration_ = false;

  std::array<float, kLidarScanBins> scan_ranges_ {};
  std::array<bool, kLidarPacketCount> scan_fragments_ {};
  std::size_t scan_fragment_count_ = 0;
  uint16_t scan_min_distance_cm_ = 2;
  uint16_t scan_max_distance_cm_ = 1200;

  const std::chrono::steady_clock::time_point start_time_ = std::chrono::steady_clock::now();
  std::atomic_bool running_ {false};
  std::thread reader_thread_;
  mutable std::mutex serial_mutex_;
  int fd_ = -1;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<MavlinkBridge>());
  rclcpp::shutdown();
  return 0;
}
