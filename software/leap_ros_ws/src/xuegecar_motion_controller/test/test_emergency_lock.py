"""Explicit emergency lock updates must reach the mux without a heartbeat."""

from threading import Lock
from unittest.mock import Mock

from std_srvs.srv import SetBool, Trigger

from xuegecar_motion_controller.node import MotionControllerNode


def test_emergency_stop_and_unlock_publish_lock_state():
    node = MotionControllerNode.__new__(MotionControllerNode)
    node._lock = Lock()
    node._mux_locked = False
    node._emergency_epoch = 0
    node._controller = Mock()
    node._controller.config.stop_publish_count = 5
    node._follow = Mock()
    node._publisher = Mock()
    node._emergency_lock_publisher = Mock()

    response = node._on_emergency_stop(Trigger.Request(), Trigger.Response())

    assert response.success
    node._controller.stop.assert_called_once()
    node._follow.cancel.assert_called_once()
    assert node._publisher.publish.call_count == 5
    assert node._emergency_lock_publisher.publish.call_args.args[0].data is True

    response = node._on_set_emergency_lock(
        SetBool.Request(data=False), SetBool.Response()
    )

    assert response.success
    assert node._emergency_lock_publisher.publish.call_args.args[0].data is False
    assert node._emergency_lock_publisher.publish.call_count == 2
