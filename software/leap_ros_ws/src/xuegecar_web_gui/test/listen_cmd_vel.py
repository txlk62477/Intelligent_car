"""测试用：订阅 /cmd_vel（twist_mux 输出），记录最近收到的 Twist。"""

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist


class CmdVelListener(Node):
    def __init__(self):
        super().__init__("cmd_vel_listener")
        self._count = 0
        self._last = None
        self._sub = self.create_subscription(Twist, "/cmd_vel", self._cb, 10)
        self._timer = self.create_timer(1.0, self._report)

    def _cb(self, msg: Twist):
        self._count += 1
        self._last = (msg.linear.x, msg.angular.z)

    def _report(self):
        print(f"cmd_vel: count={self._count} last={self._last}", flush=True)


def main():
    rclpy.init()
    node = CmdVelListener()
    rclpy.spin(node)


if __name__ == "__main__":
    main()
