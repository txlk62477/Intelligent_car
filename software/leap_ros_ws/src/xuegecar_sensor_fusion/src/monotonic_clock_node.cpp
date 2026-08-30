#include "xuegecar_sensor_fusion/raw_monotonic_time.hpp"

#include <rclcpp/rclcpp.hpp>
#include <rosgraph_msgs/msg/clock.hpp>

#include <chrono>
#include <cmath>
#include <cstdint>
#include <functional>
#include <stdexcept>

namespace xuegecar_sensor_fusion
{

class MonotonicClockNode final : public rclcpp::Node
{
public:
  MonotonicClockNode()
  : Node("monotonic_clock_node"),
    system_clock_(RCL_SYSTEM_TIME)
  {
    const double frequency = declare_parameter<double>("frequency", 100.0);
    if (!std::isfinite(frequency) || frequency <= 0.0) {
      throw std::invalid_argument("frequency must be finite and positive");
    }

    system_anchor_nanoseconds_ = system_clock_.now().nanoseconds();
    raw_anchor_nanoseconds_ = raw_monotonic_now_nanoseconds();
    last_published_nanoseconds_ = system_anchor_nanoseconds_;

    clock_publisher_ = create_publisher<rosgraph_msgs::msg::Clock>(
      "/clock", rclcpp::ClockQoS());
    const auto period = std::chrono::duration_cast<std::chrono::nanoseconds>(
      std::chrono::duration<double>(1.0 / frequency));
    timer_ = create_wall_timer(period, std::bind(&MonotonicClockNode::publish_clock, this));

    RCLCPP_INFO(
      get_logger(), "monotonic ROS clock ready: %.1f Hz, epoch anchor=%ld ns",
      frequency, static_cast<long>(system_anchor_nanoseconds_));
  }

private:
  void publish_clock()
  {
    const std::int64_t raw_elapsed =
      raw_monotonic_now_nanoseconds() - raw_anchor_nanoseconds_;
    std::int64_t logical_nanoseconds = system_anchor_nanoseconds_ + raw_elapsed;
    if (logical_nanoseconds <= last_published_nanoseconds_) {
      logical_nanoseconds = last_published_nanoseconds_ + 1;
    }

    rosgraph_msgs::msg::Clock message;
    message.clock = static_cast<builtin_interfaces::msg::Time>(
      rclcpp::Time(logical_nanoseconds, RCL_ROS_TIME));
    clock_publisher_->publish(message);
    last_published_nanoseconds_ = logical_nanoseconds;
  }

  rclcpp::Clock system_clock_;
  std::int64_t system_anchor_nanoseconds_{0};
  std::int64_t raw_anchor_nanoseconds_{0};
  std::int64_t last_published_nanoseconds_{0};
  rclcpp::Publisher<rosgraph_msgs::msg::Clock>::SharedPtr clock_publisher_;
  rclcpp::TimerBase::SharedPtr timer_;
};

}  // namespace xuegecar_sensor_fusion

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<xuegecar_sensor_fusion::MonotonicClockNode>());
  rclcpp::shutdown();
  return 0;
}
