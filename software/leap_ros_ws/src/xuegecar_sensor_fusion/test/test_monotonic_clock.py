import os
import time
import unittest

import launch
import launch_ros.actions
import launch_testing.actions
import rclpy
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from rosgraph_msgs.msg import Clock


os.environ["ROS_AUTOMATIC_DISCOVERY_RANGE"] = "LOCALHOST"


def generate_test_description():
    clock_node = launch_ros.actions.Node(
        package="xuegecar_sensor_fusion",
        executable="monotonic_clock_node",
        parameters=[{"frequency": 100.0}],
        output="screen",
    )
    return launch.LaunchDescription(
        [clock_node, launch_testing.actions.ReadyToTest()]
    )


class TestMonotonicClockOutput(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rclpy.init()
        cls.node = rclpy.create_node("test_monotonic_clock_output")

    @classmethod
    def tearDownClass(cls):
        cls.node.destroy_node()
        rclpy.shutdown()

    def test_clock_elapsed_matches_monotonic_raw(self):
        samples = []

        def receive(message):
            ros_ns = message.clock.sec * 1_000_000_000 + message.clock.nanosec
            raw_ns = time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW)
            samples.append((ros_ns, raw_ns))

        subscription = self.node.create_subscription(
            Clock,
            "/clock",
            receive,
            QoSProfile(
                depth=1,
                reliability=ReliabilityPolicy.BEST_EFFORT,
                durability=DurabilityPolicy.VOLATILE,
            ),
        )
        deadline = time.clock_gettime(time.CLOCK_MONOTONIC_RAW) + 5.0
        while time.clock_gettime(time.CLOCK_MONOTONIC_RAW) < deadline:
            rclpy.spin_once(self.node, timeout_sec=0.1)
            if samples and samples[-1][0] - samples[0][0] >= 2_000_000_000:
                break

        self.node.destroy_subscription(subscription)
        self.assertGreaterEqual(len(samples), 100)
        ros_elapsed = (samples[-1][0] - samples[0][0]) / 1.0e9
        raw_elapsed = (samples[-1][1] - samples[0][1]) / 1.0e9
        self.assertAlmostEqual(ros_elapsed / raw_elapsed, 1.0, delta=0.01)
