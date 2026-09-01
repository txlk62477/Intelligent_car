// Re-stamp the MCU-published /scan with the local ROS clock.
//
// The micro-ROS firmware stamps /scan using a fixed esp_timer->epoch offset
// captured once per session, so its timestamps drift from /clock as the
// ESP32 crystal and host clock diverge. Consumers that transform scans with
// tf2 (AMCL, costmap obstacle layers, collision monitor) then see scans
// whose stamps do not match the TF timeline, which during rotation smears
// the inflated obstacle area and makes AMCL drop laser scans.
//
// This node passes /scan through unchanged except for header.stamp, which is
// rewritten to now() so that downstream nav2 nodes operate on the same
// /clock timeline as the TF data.

#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/laser_scan.hpp>

#include <cstddef>
#include <string>

namespace xuegecar_sensor_fusion
{

class ScanRestampNode final : public rclcpp::Node
{
public:
  ScanRestampNode()
  : Node("scan_restamp")
  {
    const auto scan_in = declare_parameter<std::string>("scan_in", "/scan");
    const auto scan_out = declare_parameter<std::string>("scan_out", "/scan_ts");

    auto sensor_qos = rclcpp::SensorDataQoS().keep_last(1);

    scan_pub_ = create_publisher<sensor_msgs::msg::LaserScan>(scan_out, sensor_qos);
    scan_sub_ = create_subscription<sensor_msgs::msg::LaserScan>(
      scan_in, sensor_qos,
      [this, scan_out](sensor_msgs::msg::LaserScan::SharedPtr msg) {
        msg->header.stamp = now();
        scan_pub_->publish(*msg);
        ++published_count_;
        if (published_count_ == 1 || published_count_ % 500 == 0) {
          const double stamp_seconds =
            static_cast<double>(msg->header.stamp.sec) +
            static_cast<double>(msg->header.stamp.nanosec) * 1.0e-9;
          RCLCPP_INFO(
            get_logger(),
            "re-stamped %zu scan(s) -> %s (last stamp %.3f)",
            published_count_, scan_out.c_str(), stamp_seconds);
        }
      });

    RCLCPP_INFO(get_logger(), "re-stamping %s -> %s", scan_in.c_str(), scan_out.c_str());
  }

private:
  rclcpp::Publisher<sensor_msgs::msg::LaserScan>::SharedPtr scan_pub_;
  rclcpp::Subscription<sensor_msgs::msg::LaserScan>::SharedPtr scan_sub_;
  std::size_t published_count_{0};
};

}  // namespace xuegecar_sensor_fusion

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<xuegecar_sensor_fusion::ScanRestampNode>());
  rclcpp::shutdown();
  return 0;
}
