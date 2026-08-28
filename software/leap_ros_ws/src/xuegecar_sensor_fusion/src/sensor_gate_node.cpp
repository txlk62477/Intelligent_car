#include "xuegecar_sensor_fusion/sensor_gate.hpp"

#include <diagnostic_msgs/msg/diagnostic_array.hpp>
#include <diagnostic_msgs/msg/diagnostic_status.hpp>
#include <diagnostic_msgs/msg/key_value.hpp>
#include <nav_msgs/msg/odometry.hpp>
#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/imu.hpp>
#include <std_msgs/msg/string.hpp>

#include <chrono>
#include <cmath>
#include <cstdint>
#include <functional>
#include <memory>
#include <stdexcept>
#include <string>
#include <utility>

namespace xuegecar_sensor_fusion
{
namespace
{

constexpr double kUnusedVariance = 1.0e6;

struct GateCounters
{
  std::uint64_t received{0};
  std::uint64_t accepted{0};
  std::uint64_t rejected_non_finite{0};
  std::uint64_t rejected_magnitude{0};
  std::uint64_t rejected_rate{0};
  std::uint64_t rejected_timestamp{0};
  std::uint64_t source_stamp_anomalies{0};
  double last_receive_seconds{-1.0};
};

void add_value(
  diagnostic_msgs::msg::DiagnosticStatus & status,
  const std::string & key,
  const std::string & value)
{
  diagnostic_msgs::msg::KeyValue item;
  item.key = key;
  item.value = value;
  status.values.push_back(std::move(item));
}

std::int64_t stamp_nanoseconds(const builtin_interfaces::msg::Time & stamp)
{
  return static_cast<std::int64_t>(stamp.sec) * 1000000000LL +
         static_cast<std::int64_t>(stamp.nanosec);
}

const char * motion_phase_name(const MotionPhase phase)
{
  switch (phase) {
    case MotionPhase::kStationary:
      return "stationary";
    case MotionPhase::kStraight:
      return "straight";
    case MotionPhase::kTurning:
      return "turning";
    case MotionPhase::kArc:
      return "arc";
  }
  return "unknown";
}

}  // namespace

class SensorGateNode final : public rclcpp::Node
{
public:
  SensorGateNode()
  : Node("sensor_gate_node"),
    steady_clock_(RCL_STEADY_TIME),
    odom_gate_(load_odom_gate_config()),
    odom_angular_gate_(load_odom_angular_gate_config()),
    imu_gate_(load_imu_gate_config()),
    motion_state_machine_(load_motion_state_machine_config())
  {
    input_odom_topic_ = declare_parameter<std::string>("input_odom_topic", "/odom");
    input_imu_topic_ = declare_parameter<std::string>("input_imu_topic", "/imu");
    output_odom_topic_ =
      declare_parameter<std::string>("output_odom_topic", "/fusion/odom_valid");
    output_imu_topic_ =
      declare_parameter<std::string>("output_imu_topic", "/fusion/imu_valid");

    imu_gyro_z_bias_ = declare_parameter<double>("imu_gyro_z_bias", -0.00942);
    odom_vx_stddev_ = declare_parameter<double>("odom_vx_stddev", 0.02);
    odom_wz_stddev_ = declare_parameter<double>("odom_wz_stddev", 0.04);
    turn_odom_wz_stddev_ = declare_parameter<double>("turn_odom_wz_stddev", 0.16);
    arc_odom_wz_stddev_ = declare_parameter<double>("arc_odom_wz_stddev", 0.08);
    imu_gyro_z_stddev_ = declare_parameter<double>("imu_gyro_z_stddev", 0.08);
    retimestamp_with_receipt_time_ =
      declare_parameter<bool>("retimestamp_with_receipt_time", true);
    require_monotonic_stamp_ = declare_parameter<bool>("require_monotonic_stamp", true);
    max_source_stamp_offset_ = declare_parameter<double>("max_source_stamp_offset", 0.25);
    diagnostic_timeout_ = declare_parameter<double>("diagnostic_timeout", 0.3);
    const double diagnostic_period = declare_parameter<double>("diagnostic_period", 1.0);

    validate_positive_finite("odom_vx_stddev", odom_vx_stddev_);
    validate_positive_finite("odom_wz_stddev", odom_wz_stddev_);
    validate_positive_finite("turn_odom_wz_stddev", turn_odom_wz_stddev_);
    validate_positive_finite("arc_odom_wz_stddev", arc_odom_wz_stddev_);
    validate_positive_finite("imu_gyro_z_stddev", imu_gyro_z_stddev_);
    validate_positive_finite("max_source_stamp_offset", max_source_stamp_offset_);
    validate_positive_finite("diagnostic_timeout", diagnostic_timeout_);
    validate_positive_finite("diagnostic_period", diagnostic_period);
    if (!std::isfinite(imu_gyro_z_bias_)) {
      throw std::invalid_argument("imu_gyro_z_bias must be finite");
    }

    const auto odom_qos = rclcpp::QoS(rclcpp::KeepLast(50)).reliable();
    odom_publisher_ = create_publisher<nav_msgs::msg::Odometry>(output_odom_topic_, odom_qos);
    imu_publisher_ = create_publisher<sensor_msgs::msg::Imu>(
      output_imu_topic_, rclcpp::SensorDataQoS());
    diagnostics_publisher_ = create_publisher<diagnostic_msgs::msg::DiagnosticArray>(
      "/diagnostics", rclcpp::QoS(10).reliable());
    motion_phase_publisher_ = create_publisher<std_msgs::msg::String>(
      "/fusion/motion_phase", rclcpp::QoS(rclcpp::KeepLast(10)).reliable());

    odom_subscription_ = create_subscription<nav_msgs::msg::Odometry>(
      input_odom_topic_, odom_qos,
      std::bind(&SensorGateNode::handle_odom, this, std::placeholders::_1));
    imu_subscription_ = create_subscription<sensor_msgs::msg::Imu>(
      input_imu_topic_, rclcpp::SensorDataQoS(),
      std::bind(&SensorGateNode::handle_imu, this, std::placeholders::_1));

    const auto period = std::chrono::duration_cast<std::chrono::milliseconds>(
      std::chrono::duration<double>(diagnostic_period));
    diagnostic_timer_ = create_wall_timer(period, std::bind(&SensorGateNode::publish_diagnostics, this));

    RCLCPP_INFO(
      get_logger(),
      "sensor gate ready: %s -> %s, %s -> %s",
      input_odom_topic_.c_str(), output_odom_topic_.c_str(),
      input_imu_topic_.c_str(), output_imu_topic_.c_str());
  }

private:
  ScalarGateConfig load_odom_gate_config()
  {
    ScalarGateConfig config;
    config.max_abs_value = declare_parameter<double>("max_abs_linear_velocity", 1.0);
    config.max_abs_rate = declare_parameter<double>("max_abs_linear_acceleration", 10.0);
    config.min_rate_dt = declare_parameter<double>("min_derivative_dt", 0.01);
    config.reset_rate_after_gap = declare_parameter<double>("reset_derivative_after_gap", 0.5);
    return config;
  }

  ScalarGateConfig load_imu_gate_config()
  {
    ScalarGateConfig config;
    config.max_abs_value = declare_parameter<double>("max_abs_gyro_z", 4.0);
    config.max_abs_rate = 0.0;
    config.min_rate_dt = 0.0;
    config.reset_rate_after_gap = 0.0;
    return config;
  }

  ScalarGateConfig load_odom_angular_gate_config()
  {
    ScalarGateConfig config;
    config.max_abs_value = declare_parameter<double>("max_abs_odom_angular_velocity", 4.0);
    config.max_abs_rate = 0.0;
    config.min_rate_dt = 0.0;
    config.reset_rate_after_gap = 0.0;
    return config;
  }

  MotionStateMachineConfig load_motion_state_machine_config()
  {
    MotionStateMachineConfig config;
    config.stationary_max_abs_linear_velocity =
      declare_parameter<double>("stationary_linear_velocity_threshold", 0.005);
    config.stationary_max_abs_angular_velocity =
      declare_parameter<double>("stationary_angular_velocity_threshold", 0.01);
    config.stationary_hold_duration =
      declare_parameter<double>("stationary_hold_duration", 0.2);
    config.turn_enter_angular_velocity_threshold =
      declare_parameter<double>("turn_enter_angular_velocity_threshold", 0.15);
    config.turn_exit_angular_velocity_threshold =
      declare_parameter<double>("turn_exit_angular_velocity_threshold", 0.10);
    config.enter_hold_duration =
      declare_parameter<double>("enter_hold_duration", 0.1);
    config.exit_hold_duration =
      declare_parameter<double>("exit_hold_duration", 0.2);
    config.arc_min_abs_linear_velocity =
      declare_parameter<double>("arc_min_abs_linear_velocity", 0.05);
    config.arc_exit_abs_linear_velocity =
      declare_parameter<double>("arc_exit_abs_linear_velocity", 0.03);
    return config;
  }

  static void validate_positive_finite(const char * name, const double value)
  {
    if (!std::isfinite(value) || value <= 0.0) {
      throw std::invalid_argument(std::string(name) + " must be finite and positive");
    }
  }

  bool source_stamp_is_acceptable(
    const builtin_interfaces::msg::Time & stamp,
    const std::int64_t receipt_nanoseconds,
    std::int64_t & last_stamp,
    bool & has_last_stamp,
    GateCounters & counters)
  {
    const std::int64_t current = stamp_nanoseconds(stamp);
    const auto offset = std::abs(receipt_nanoseconds - current);
    const auto max_offset = static_cast<std::int64_t>(max_source_stamp_offset_ * 1.0e9);
    const bool anomaly = current <= 0 || offset > max_offset ||
      (has_last_stamp && current <= last_stamp);
    if (anomaly) {
      ++counters.source_stamp_anomalies;
      if (require_monotonic_stamp_ && !retimestamp_with_receipt_time_) {
        ++counters.rejected_timestamp;
        return false;
      }
    } else {
      last_stamp = current;
      has_last_stamp = true;
    }
    return true;
  }

  static void count_rejection(const GateResult result, GateCounters & counters)
  {
    switch (result) {
      case GateResult::kNonFinite:
        ++counters.rejected_non_finite;
        break;
      case GateResult::kMagnitude:
        ++counters.rejected_magnitude;
        break;
      case GateResult::kRate:
        ++counters.rejected_rate;
        break;
      case GateResult::kNonMonotonicTime:
        ++counters.rejected_timestamp;
        break;
      case GateResult::kAccepted:
        break;
    }
  }

  void release_motion_constraints()
  {
    motion_state_machine_.reset();
    motion_phase_ = MotionPhase::kStraight;
    stationary_constraint_active_ = false;
  }

  bool stationary_constraint_is_fresh(const double now_seconds) const
  {
    return stationary_constraint_active_ && odom_counters_.last_receive_seconds >= 0.0 &&
           now_seconds - odom_counters_.last_receive_seconds <= diagnostic_timeout_;
  }

  void handle_odom(const nav_msgs::msg::Odometry::SharedPtr message)
  {
    const rclcpp::Time steady_receipt_time = steady_clock_.now();
    const rclcpp::Time receipt_time = now();
    const double receipt_seconds = steady_receipt_time.seconds();
    ++odom_counters_.received;
    odom_counters_.last_receive_seconds = receipt_seconds;

    if (!source_stamp_is_acceptable(
        message->header.stamp, receipt_time.nanoseconds(), last_odom_source_stamp_,
        has_last_odom_source_stamp_, odom_counters_))
    {
      release_motion_constraints();
      return;
    }

    const GateResult linear_result = odom_gate_.evaluate(
      message->twist.twist.linear.x, receipt_seconds);
    if (linear_result != GateResult::kAccepted) {
      count_rejection(linear_result, odom_counters_);
      release_motion_constraints();
      return;
    }
    const GateResult angular_result = odom_angular_gate_.evaluate(
      message->twist.twist.angular.z, receipt_seconds);
    if (angular_result != GateResult::kAccepted) {
      count_rejection(angular_result, odom_counters_);
      release_motion_constraints();
      return;
    }

    const MotionPhase phase = motion_state_machine_.update(
      message->twist.twist.linear.x, message->twist.twist.angular.z, receipt_seconds);
    motion_phase_ = phase;
    stationary_constraint_active_ = phase == MotionPhase::kStationary;

    auto output = *message;
    if (retimestamp_with_receipt_time_) {
      output.header.stamp = receipt_time;
    }
    // EKF 使用轮式 vx 和 vyaw。将 MCU 位姿及其余速度维度明确标为未使用，
    // 避免全 0 协方差被其他消费者误解为“完全可信”。
    if (stationary_constraint_active_) {
      output.twist.twist.angular.z = 0.0;
    }
    output.pose.covariance.fill(0.0);
    for (const std::size_t index : {0U, 7U, 14U, 21U, 28U, 35U}) {
      output.pose.covariance[index] = kUnusedVariance;
    }
    output.twist.covariance.fill(0.0);
    output.twist.covariance[0] = odom_vx_stddev_ * odom_vx_stddev_;
    // 运动相位决定轮式角速度的置信度：直行 4:1 信轮式，原地转弯 1:4 信陀螺，
    // 弧线 1:1 各半（轮子打滑时轮式角速度系统性偏大）。
    double wz_stddev = odom_wz_stddev_;
    if (motion_phase_ == MotionPhase::kTurning) {
      wz_stddev = turn_odom_wz_stddev_;
    } else if (motion_phase_ == MotionPhase::kArc) {
      wz_stddev = arc_odom_wz_stddev_;
    }
    output.twist.covariance[35] = wz_stddev * wz_stddev;
    for (const std::size_t index : {7U, 14U, 21U, 28U}) {
      output.twist.covariance[index] = kUnusedVariance;
    }
    odom_publisher_->publish(output);
    ++odom_counters_.accepted;

    std_msgs::msg::String phase_message;
    phase_message.data = motion_phase_name(motion_phase_);
    motion_phase_publisher_->publish(phase_message);
  }

  void handle_imu(const sensor_msgs::msg::Imu::SharedPtr message)
  {
    const rclcpp::Time steady_receipt_time = steady_clock_.now();
    const rclcpp::Time receipt_time = now();
    const double receipt_seconds = steady_receipt_time.seconds();
    ++imu_counters_.received;
    imu_counters_.last_receive_seconds = receipt_seconds;

    if (!source_stamp_is_acceptable(
        message->header.stamp, receipt_time.nanoseconds(), last_imu_source_stamp_,
        has_last_imu_source_stamp_, imu_counters_))
    {
      return;
    }

    const GateResult result = imu_gate_.evaluate(message->angular_velocity.z, receipt_seconds);
    if (result != GateResult::kAccepted) {
      count_rejection(result, imu_counters_);
      return;
    }

    auto output = *message;
    if (retimestamp_with_receipt_time_) {
      output.header.stamp = receipt_time;
    }
    // imu_link 与 base_link 固定关节且 rpy=0 完全对齐（URDF imu_joint）。
    // 直接声明为 base_link，避免 EKF 因缺少 imu_link->base_link TF 丢弃 IMU。
    output.header.frame_id = "base_link";
    output.angular_velocity.z -= imu_gyro_z_bias_;
    if (stationary_constraint_is_fresh(receipt_seconds)) {
      output.angular_velocity.z = 0.0;
    }
    output.angular_velocity_covariance.fill(0.0);
    output.angular_velocity_covariance[0] = kUnusedVariance;
    output.angular_velocity_covariance[4] = kUnusedVariance;
    output.angular_velocity_covariance[8] = imu_gyro_z_stddev_ * imu_gyro_z_stddev_;
    imu_publisher_->publish(output);
    ++imu_counters_.accepted;
  }

  diagnostic_msgs::msg::DiagnosticStatus make_status(
    const std::string & sensor,
    const GateCounters & counters,
    const double now_seconds) const
  {
    diagnostic_msgs::msg::DiagnosticStatus status;
    status.name = get_fully_qualified_name() + std::string("/") + sensor + "_gate";
    status.hardware_id = "xuegecar";

    const double age = counters.last_receive_seconds < 0.0 ?
      -1.0 : now_seconds - counters.last_receive_seconds;
    if (counters.received == 0) {
      status.level = diagnostic_msgs::msg::DiagnosticStatus::WARN;
      status.message = "waiting for data";
    } else if (age > diagnostic_timeout_) {
      status.level = diagnostic_msgs::msg::DiagnosticStatus::ERROR;
      status.message = "sensor data stale";
    } else {
      status.level = diagnostic_msgs::msg::DiagnosticStatus::OK;
      status.message = "OK";
    }

    add_value(status, "received", std::to_string(counters.received));
    add_value(status, "accepted", std::to_string(counters.accepted));
    add_value(status, "rejected_non_finite", std::to_string(counters.rejected_non_finite));
    add_value(status, "rejected_magnitude", std::to_string(counters.rejected_magnitude));
    add_value(status, "rejected_rate", std::to_string(counters.rejected_rate));
    add_value(status, "rejected_timestamp", std::to_string(counters.rejected_timestamp));
    add_value(status, "source_stamp_anomalies", std::to_string(counters.source_stamp_anomalies));
    add_value(status, "last_message_age_seconds", std::to_string(age));
    return status;
  }

  void publish_diagnostics()
  {
    const rclcpp::Time steady_current_time = steady_clock_.now();
    diagnostic_msgs::msg::DiagnosticArray array;
    array.header.stamp = now();
    auto odom_status = make_status("odom", odom_counters_, steady_current_time.seconds());
    add_value(
      odom_status, "stationary_constraint_active",
      stationary_constraint_is_fresh(steady_current_time.seconds()) ? "true" : "false");
    add_value(odom_status, "motion_phase", motion_phase_name(motion_phase_));
    array.status.push_back(std::move(odom_status));
    array.status.push_back(make_status("imu", imu_counters_, steady_current_time.seconds()));
    diagnostics_publisher_->publish(array);
  }

  rclcpp::Clock steady_clock_;
  ScalarGate odom_gate_;
  ScalarGate odom_angular_gate_;
  ScalarGate imu_gate_;
  MotionStateMachine motion_state_machine_;

  std::string input_odom_topic_;
  std::string input_imu_topic_;
  std::string output_odom_topic_;
  std::string output_imu_topic_;
  double imu_gyro_z_bias_{0.0};
  double odom_vx_stddev_{0.02};
  double odom_wz_stddev_{0.04};
  double turn_odom_wz_stddev_{0.16};
  double arc_odom_wz_stddev_{0.08};
  double imu_gyro_z_stddev_{0.08};
  double max_source_stamp_offset_{0.25};
  double diagnostic_timeout_{0.3};
  bool retimestamp_with_receipt_time_{true};
  bool require_monotonic_stamp_{true};
  bool stationary_constraint_active_{false};
  MotionPhase motion_phase_{MotionPhase::kStraight};

  GateCounters odom_counters_;
  GateCounters imu_counters_;
  std::int64_t last_odom_source_stamp_{0};
  std::int64_t last_imu_source_stamp_{0};
  bool has_last_odom_source_stamp_{false};
  bool has_last_imu_source_stamp_{false};

  rclcpp::Publisher<nav_msgs::msg::Odometry>::SharedPtr odom_publisher_;
  rclcpp::Publisher<sensor_msgs::msg::Imu>::SharedPtr imu_publisher_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr motion_phase_publisher_;
  rclcpp::Publisher<diagnostic_msgs::msg::DiagnosticArray>::SharedPtr diagnostics_publisher_;
  rclcpp::Subscription<nav_msgs::msg::Odometry>::SharedPtr odom_subscription_;
  rclcpp::Subscription<sensor_msgs::msg::Imu>::SharedPtr imu_subscription_;
  rclcpp::TimerBase::SharedPtr diagnostic_timer_;
};

}  // namespace xuegecar_sensor_fusion

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  try {
    rclcpp::spin(std::make_shared<xuegecar_sensor_fusion::SensorGateNode>());
  } catch (const std::exception & error) {
    RCLCPP_FATAL(rclcpp::get_logger("sensor_gate_node"), "%s", error.what());
    rclcpp::shutdown();
    return 1;
  }
  rclcpp::shutdown();
  return 0;
}
