// Run Nav2's real scan conversion and velocity processing without robot inputs.
#include <cmath>
#include <cstring>
#include <fstream>
#include <limits>
#include <memory>
#include <string>
#include <thread>

#include "gtest/gtest.h"
#include "xuegecar_bringup/safe_collision_monitor.hpp"

namespace
{
using nav2_collision_monitor::Velocity;

template<typename Message>
std::shared_ptr<Message> fixture(const std::string & name)
{
  std::ifstream stream(std::string(COLLISION_FIXTURES) + "/" + name, std::ios::binary);
  if (!stream) {
    throw std::runtime_error("Missing recorded collision fixture: " + name);
  }
  std::vector<char> bytes((std::istreambuf_iterator<char>(stream)),
    std::istreambuf_iterator<char>());
  rclcpp::SerializedMessage serialized(bytes.size());
  auto & buffer = serialized.get_rcl_serialized_message();
  std::memcpy(buffer.buffer, bytes.data(), bytes.size());
  buffer.buffer_length = bytes.size();
  auto message = std::make_shared<Message>();
  rclcpp::Serialization<Message>().deserialize_message(&serialized, message.get());
  return message;
}

class MonitorProbe : public xuegecar_bringup::SafeCollisionMonitor
{
public:
  explicit MonitorProbe(const rclcpp::NodeOptions & options)
  : SafeCollisionMonitor(options) {}

  void start()
  {
    ASSERT_EQ(on_configure(get_current_state()), nav2_util::CallbackReturn::SUCCESS);
    for (const auto & frames : {
        std::make_pair("odom", "base_link"),
        std::make_pair("base_link", "laser_frame")})
    {
      geometry_msgs::msg::TransformStamped tf;
      tf.header.stamp = now();
      tf.header.frame_id = frames.first;
      tf.child_frame_id = frames.second;
      const bool laser = tf.child_frame_id == "laser_frame";
      tf.transform.translation.x = laser ? 0.020 : 0.0;
      tf.transform.translation.z = laser ? 0.1055 : 0.0;
      // Deliberate sensor rotation verifies the full scan-to-body TF conversion.
      tf.transform.rotation.z = laser ? std::sin(0.15) : 0.0;
      tf.transform.rotation.w = laser ? std::cos(0.15) : 1.0;
      ASSERT_TRUE(tf_buffer_->setTransform(tf, "test", true));
    }
    ASSERT_EQ(on_activate(get_current_state()), nav2_util::CallbackReturn::SUCCESS);
    odometry(0.0);
  }

  void odometry(double vx, double wz = 0.0)
  {
    auto message = std::make_shared<nav_msgs::msg::Odometry>();
    message->twist.twist.linear.x = vx;
    message->twist.twist.angular.z = wz;
    receiveOdometry(message);
  }

  void recordedOdometry()
  {
    receiveOdometry(fixture<nav_msgs::msg::Odometry>("front_overlap_odom.cdr"));
  }

  void sensorYaw(double yaw)
  {
    geometry_msgs::msg::TransformStamped tf;
    tf.header.stamp = now();
    tf.header.frame_id = "base_link";
    tf.child_frame_id = "laser_frame";
    tf.transform.translation.x = 0.020;
    tf.transform.translation.z = 0.1055;
    tf.transform.rotation.z = std::sin(yaw / 2);
    tf.transform.rotation.w = std::cos(yaw / 2);
    ASSERT_TRUE(tf_buffer_->setTransform(tf, "test", true));
  }

  Velocity commandScan(
    sensor_msgs::msg::LaserScan::SharedPtr scan, double vx,
    double wz = 0.0, double vy = 0.0, bool restamp = true)
  {
    if (restamp) {
      scan->header.stamp = now();
    }
    escape_scan_->receive(scan);
    processSafe({vx, vy, wz}, std_msgs::msg::Header());
    return robot_action_prev_.req_vel;
  }

  Velocity checkWatchdog()
  {
    watchdog();
    return robot_action_prev_.req_vel;
  }

  Velocity command(double obstacle_x, double vx, double wz = 0.0, int count = 3)
  {
    auto scan = std::make_shared<sensor_msgs::msg::LaserScan>();
    scan->header.stamp = now();
    scan->header.frame_id = "laser_frame";
    const double bearing = obstacle_x > 0.020 ? 0.0 : std::acos(-1.0);
    scan->angle_min = bearing - 0.3 - 0.01;
    scan->angle_increment = 0.01;
    scan->angle_max = scan->angle_min + 0.02;
    scan->range_min = 0.001;
    scan->range_max = 10.0;
    scan->ranges.assign(3, std::numeric_limits<float>::infinity());
    for (int i = 0; i < count; ++i) {
      scan->ranges[i] = std::abs(obstacle_x - 0.020);
    }
    return commandScan(scan, vx, wz);
  }

};

class CollisionEscape : public testing::Test
{
protected:
  void SetUp() override
  {
    rclcpp::NodeOptions options;
    options.arguments({
        "--ros-args", "--params-file", COLLISION_CONFIG,
        "-p", "use_sim_time:=false",
        "-p", "cmd_vel_in_topic:=/collision_regression/input",
        "-p", "cmd_vel_out_topic:=/collision_regression/output",
        "-p", "state_topic:=/collision_regression/state",
        "-p", "scan.topic:=/collision_regression/scan",
        "-p", "odom_topic:=/collision_regression/odom",
        "-p", "source_timeout:=0.15",
        "-p", "escape_odom_timeout:=0.15",
        "-p", "command_timeout:=0.1"});
    monitor = std::make_shared<MonitorProbe>(options);
    monitor->start();
  }

  void expect(
    double x, double vx, double expected_vx, double wz = 0.0,
    double expected_wz = 0.0, int count = 3)
  {
    const auto output = monitor->command(x, vx, wz, count);
    EXPECT_DOUBLE_EQ(output.x, expected_vx);
    EXPECT_DOUBLE_EQ(output.y, 0.0);
    EXPECT_DOUBLE_EQ(output.tw, expected_wz);
  }

  std::shared_ptr<MonitorProbe> monitor;
};

TEST_F(CollisionEscape, FrontStopThenReverseEscape)
{
  expect(0.134, 0.05, 0.0);
  expect(0.134, -0.05, -0.03);
}

TEST_F(CollisionEscape, RearStopThenForwardEscape)
{
  expect(-0.0425, -0.05, 0.0);
  expect(-0.0425, 0.05, 0.03);
}

TEST_F(CollisionEscape, RotationAndCurvedMotionKeepSurroundingProtection)
{
  expect(0.134, 0.0, 0.0, 0.3);
  expect(0.134, 0.0, 0.0, -0.3);
  expect(0.134, -0.05, 0.0, 0.3);
}

TEST_F(CollisionEscape, ShallowFrontOverlapAllowsOnlySlowReverse)
{
  expect(0.120, 0.05, 0.0);
  expect(0.120, -0.05, -0.03);
}

TEST_F(CollisionEscape, HigherSpeedStartsPredictionFurtherAway)
{
  const auto fast = monitor->command(0.320, 0.30);
  EXPECT_GT(fast.x, 0.0);
  EXPECT_LT(fast.x, 0.30);
  expect(0.320, 0.05, 0.05);
}

TEST_F(CollisionEscape, RecordedFrontOverlapCanEscapeAtThreeCmPerSecond)
{
  monitor->sensorYaw(0.0);
  monitor->recordedOdometry();
  auto scan = fixture<sensor_msgs::msg::LaserScan>("front_overlap_scan.cdr");
  EXPECT_DOUBLE_EQ(monitor->commandScan(scan, 0.3).x, 0.0);
  EXPECT_DOUBLE_EQ(monitor->commandScan(scan, -0.3).x, -0.03);
  EXPECT_DOUBLE_EQ(monitor->commandScan(scan, -0.01).x, -0.01);
  EXPECT_DOUBLE_EQ(monitor->commandScan(scan, 0.0, 1.0).tw, 0.0);
  EXPECT_TRUE(monitor->commandScan(scan, -0.3, 0.0, 0.01).isZero());
}

TEST_F(CollisionEscape, FrontAndRearOverlapCannotEscape)
{
  monitor->sensorYaw(0.0);
  auto scan = fixture<sensor_msgs::msg::LaserScan>("front_overlap_scan.cdr");
  for (int index : {179, 180, 181}) {
    scan->ranges[index] = 0.070;
  }
  EXPECT_TRUE(monitor->commandScan(scan, -0.3).isZero());
  EXPECT_TRUE(monitor->commandScan(scan, 0.3).isZero());
}

TEST_F(CollisionEscape, EscapeStillPredictsRearObstacleOutsideHardZone)
{
  monitor->sensorYaw(0.0);
  auto scan = fixture<sensor_msgs::msg::LaserScan>("front_overlap_scan.cdr");
  for (int index : {179, 180, 181}) {
    scan->ranges[index] = 0.090;
  }
  const auto output = monitor->commandScan(scan, -0.3);
  EXPECT_LT(output.x, 0.0);
  EXPECT_GT(output.x, -0.03);
}

TEST_F(CollisionEscape, SideOverlapCannotEscape)
{
  monitor->sensorYaw(0.0);
  auto scan = fixture<sensor_msgs::msg::LaserScan>("front_overlap_scan.cdr");
  for (int index : {69, 70, 71}) {
    scan->ranges[index] = 0.090;
  }
  EXPECT_TRUE(monitor->commandScan(scan, -0.3).isZero());
}

TEST_F(CollisionEscape, DeepBodyOverlapCannotEscape)
{
  expect(0.100, 0.05, 0.0);
  expect(0.100, -0.05, 0.0);
}

TEST_F(CollisionEscape, EscapeWaitsForMeasuredMotionToStop)
{
  monitor->odometry(0.2);
  expect(0.134, -0.3, 0.0);
  monitor->odometry(0.0, 0.2);
  expect(0.134, -0.3, 0.0);
  monitor->odometry(0.0);
  expect(0.134, -0.3, -0.03);
}

TEST_F(CollisionEscape, StaleOdometryCannotAuthorizeEscape)
{
  std::this_thread::sleep_for(std::chrono::milliseconds(180));
  expect(0.134, -0.3, 0.0);
}

TEST_F(CollisionEscape, StaleScanStopsWithoutAnotherCommand)
{
  expect(1.0, 0.3, 0.3);
  std::this_thread::sleep_for(std::chrono::milliseconds(180));
  EXPECT_TRUE(monitor->checkWatchdog().isZero());
}

TEST_F(CollisionEscape, OldScanTimestampCannotAuthorizeEscape)
{
  monitor->sensorYaw(0.0);
  auto scan = fixture<sensor_msgs::msg::LaserScan>("front_overlap_scan.cdr");
  scan->header.stamp = monitor->now() - rclcpp::Duration::from_seconds(2.0);
  EXPECT_TRUE(monitor->commandScan(scan, -0.3, 0.0, 0.0, false).isZero());
}

TEST_F(CollisionEscape, MissingLaserTfCannotAuthorizeEscape)
{
  auto scan = fixture<sensor_msgs::msg::LaserScan>("front_overlap_scan.cdr");
  scan->header.frame_id = "missing_laser_frame";
  EXPECT_TRUE(monitor->commandScan(scan, -0.3).isZero());
}

TEST_F(CollisionEscape, FewerThanThreePointsDoNotStop)
{
  expect(0.134, 0.01, 0.01, 0.0, 0.0, 2);
}

TEST_F(CollisionEscape, OutsideTwoCmBufferIsClearAtLowSpeed)
{
  expect(0.160, 0.01, 0.01);
}

TEST_F(CollisionEscape, TtcApproachStillSlowsBeforeEmergencyZone)
{
  const auto output = monitor->command(0.150, 0.15);
  EXPECT_GT(output.x, 0.0);
  EXPECT_LT(output.x, 0.15);
}
}  // namespace

int main(int argc, char ** argv)
{
  testing::InitGoogleTest(&argc, argv);
  rclcpp::init(argc, argv);
  const int result = RUN_ALL_TESTS();
  rclcpp::shutdown();
  return result;
}
