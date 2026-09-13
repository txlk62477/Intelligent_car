"""Exercise the actual command/stop state machine without publishing to a robot."""

import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from xuegecar_web_gui.node import WebGuiNode


@pytest.fixture
def node(monkeypatch):
    now = [10.0]
    monkeypatch.setattr('xuegecar_web_gui.node.time.monotonic', lambda: now[0])
    instance = WebGuiNode.__new__(WebGuiNode)
    instance._lock = threading.Lock()
    instance._session = {'active': True, 'lease_deadline': 10.1}
    instance._estop_locked = False
    instance.max_linear_cap = 2.0
    instance.max_angular_cap = 5.0
    instance.command_timeout = 0.1
    instance._period = 0.025
    instance._state = 'IDLE'
    instance._cmd_linear = instance._cmd_angular = 0.0
    instance._last_cmd_mono = instance._command_expires_mono = 0.0
    instance._next_publish_mono = 0.0
    instance._stop_ticks = 0
    instance._actions = []
    instance._cmd_pub = Mock()
    return SimpleNamespace(backend=instance, now=now)


def test_lost_stop_expires_and_publishes_zero(node):
    backend = node.backend
    assert backend.set_command(0.0, 1.0, 10.1)
    backend._control_tick()
    assert backend._cmd_pub.publish.call_args.args[0].angular.z == 1.0
    node.now[0] = 10.1
    backend._control_tick()
    msg = backend._cmd_pub.publish.call_args.args[0]
    assert msg.linear.x == msg.angular.z == 0.0
    for _ in range(5):
        node.now[0] += 0.026
        backend._control_tick()
    assert backend._state == 'IDLE'
    count = backend._cmd_pub.publish.call_count
    backend._control_tick()
    assert backend._cmd_pub.publish.call_count == count


def test_delayed_valid_command_receives_full_motion_lease(node):
    backend = node.backend
    node.now[0] = 10.08
    assert backend.set_command(0.3, 0.0, 10.1)
    assert backend._command_expires_mono == pytest.approx(10.18)
    node.now[0] = 10.11
    backend._control_tick()
    assert backend._cmd_pub.publish.call_args.args[0].linear.x == 0.3
    node.now[0] = 10.181
    backend._control_tick()
    assert backend._cmd_pub.publish.call_args.args[0].linear.x == 0.0


def test_continuous_renewal_with_network_latency_and_direction_change(node):
    backend = node.backend
    # State + command travel consumed 70ms of the freshness window each time.
    for tick in range(80):
        node.now[0] = 10.08 + tick * 0.025
        deadline = node.now[0] + 0.03
        backend._session['lease_deadline'] = deadline
        linear, angular = (0.3, 0.0) if tick < 40 else (0.0, -1.0)
        assert backend.set_command(linear, angular, deadline)
        assert backend._command_expires_mono == pytest.approx(node.now[0] + 0.1)
        backend._control_tick()
        node.now[0] += 0.02
        backend._control_tick()
        assert backend._state == 'ACTIVE'
    emitted = [call.args[0] for call in backend._cmd_pub.publish.call_args_list]
    assert emitted
    assert all(msg.linear.x != 0 or msg.angular.z != 0 for msg in emitted)
    assert any(msg.angular.z == -1.0 for msg in emitted)
    node.now[0] = backend._command_expires_mono + 0.001
    backend._control_tick()
    assert backend._cmd_pub.publish.call_args.args[0].angular.z == 0.0


@pytest.mark.parametrize('deadline', [None, 'bad', float('nan'), float('inf'), 9.9, 10.2])
def test_missing_expired_or_unissued_lease_is_rejected(node, deadline):
    assert not node.backend.set_command(0.3, 0.0, deadline)
    assert node.backend._state == 'IDLE'


def test_delayed_packets_cannot_restart_after_expiry(node):
    backend = node.backend
    assert backend.set_command(0.3, 0.0, 10.1)
    node.now[0] = 10.11
    backend._control_tick()
    # New state packets have issued a fresh lease; queued old cmd still expires.
    backend._session['lease_deadline'] = 10.21
    for _ in range(10):
        assert not backend.set_command(0.3, 0.0, 10.1)
    backend._control_tick()
    assert backend._state != 'ACTIVE'
    assert backend._cmd_linear == 0.0


def test_stop_and_estop_clear_command_immediately(node):
    backend = node.backend
    assert backend.set_command(0.3, 0.0, 10.1)
    backend.request_stop()
    backend._control_tick()
    assert backend._cmd_pub.publish.call_args.args[0].linear.x == 0.0
    assert backend.set_command(0.3, 0.0, 10.1)
    backend.enqueue_estop()
    assert not backend.set_command(0.3, 0.0, 10.1)
    backend._control_tick()
    assert backend._cmd_pub.publish.call_args.args[0].linear.x == 0.0


def test_blocked_service_action_does_not_block_watchdog(node):
    backend = node.backend
    entered, release = threading.Event(), threading.Event()

    def blocked_service():
        entered.set()
        release.wait(2.0)

    backend._handle_unlock = blocked_service
    backend._actions = [{'type': 'unlock'}]
    assert backend.set_command(0.3, 0.0, 10.1)
    worker = threading.Thread(target=backend._action_tick)
    worker.start()
    try:
        assert entered.wait(1.0)
        node.now[0] = 10.1
        backend._control_tick()
        assert backend._cmd_pub.publish.call_args.args[0].linear.x == 0.0
        assert worker.is_alive()
    finally:
        release.set()
        worker.join(1.0)


def test_real_executor_stops_with_frozen_ros_clock_and_blocked_service():
    import time
    import rclpy
    from rclpy.executors import MultiThreadedExecutor

    # Isolated topics and unavailable services; never publish to the robot.
    rclpy.init(args=[
        '--ros-args', '-r', '__ns:=/lease_regression',
        '-p', 'cmd_vel_topic:=/lease_regression/cmd_vel',
        '-p', 'use_sim_time:=true',
        '-p', 'unlock_service:=/lease_regression/missing_unlock',
    ])
    backend = WebGuiNode()
    stopped, service_entered = threading.Event(), threading.Event()
    service_finished = threading.Event()
    emitted = []

    def record(msg):
        emitted.append((time.monotonic(), msg.linear.x, msg.angular.z))
        if msg.linear.x == msg.angular.z == 0.0:
            stopped.set()

    backend._cmd_pub = Mock()
    backend._cmd_pub.publish.side_effect = record
    original_unlock = backend._handle_unlock

    def blocking_unlock():
        service_entered.set()
        try:
            original_unlock()
        finally:
            service_finished.set()

    backend._handle_unlock = blocking_unlock
    executor = MultiThreadedExecutor(num_threads=3)
    executor.add_node(backend)
    worker = threading.Thread(target=executor.spin)
    worker.start()
    try:
        backend.try_acquire_session('test')
        backend.enqueue_unlock()
        assert service_entered.wait(1.0)
        state = backend.state_snapshot()
        started = time.monotonic()
        assert backend.set_command(0.3, 0.0, state['lease_deadline'])
        # Fresh commands with a 60ms round trip must renew a full 100ms,
        # even while the service action is waiting and ROS time is frozen.
        for _ in range(12):
            state = backend.state_snapshot()
            time.sleep(0.06)
            assert backend.set_command(0.3, 0.0, state['lease_deadline'])
            assert not stopped.is_set(), '持续续约期间不应发布零速'
        started = time.monotonic()
        assert stopped.wait(0.3), '冻结 /clock 或服务等待时租期仍须到期停车'
        assert any(linear == 0.3 for _, linear, _ in emitted)
        assert emitted[-1][1:] == (0.0, 0.0)
        assert emitted[-1][0] - started < 0.3
    finally:
        service_finished.wait(2.0)
        backend._timer.cancel()
        backend._action_timer.cancel()
        executor.remove_node(backend)
        executor.shutdown(timeout_sec=2.0)
        worker.join(2.0)
        backend.destroy_node()
        rclpy.shutdown()
