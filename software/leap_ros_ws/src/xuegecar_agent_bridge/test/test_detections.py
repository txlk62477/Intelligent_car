"""DetectionCache 过滤、排序、过期与位置归类测试。"""

from __future__ import annotations

import time

from xuegecar_agent_bridge.detections import EMPTY, DetectionCache, box_position


def _item(label: str, score: float) -> dict:
    return {"label": label, "score": score}


def test_fresh_returns_detections_sorted_and_capped():
    cache = DetectionCache(min_score=0.0, max_items=2)
    cache.store(
        [
            _item("cup", 0.6),
            _item("person", 0.95),
            _item("bottle", 0.8),
        ],
        640,
        480,
    )

    result = cache.fresh()

    assert result is not None
    assert result["status"] == "DETECTED"
    assert [item["label"] for item in result["detections"]] == ["person", "bottle"]
    assert result["image_width"] == 640


def test_fresh_filters_below_min_score_and_reports_empty():
    cache = DetectionCache(min_score=0.5)
    cache.store([_item("cup", 0.3)], 640, 480)

    result = cache.fresh()

    assert result is not None
    assert result["status"] == EMPTY
    assert result["detections"] == []


def test_fresh_returns_none_before_any_frame():
    cache = DetectionCache()

    assert cache.fresh() is None


def test_fresh_returns_none_after_max_age():
    cache = DetectionCache(max_age=0.05)
    cache.store([_item("cup", 0.9)], 640, 480)
    time.sleep(0.08)

    assert cache.fresh() is None


def test_box_position_buckets_by_center():
    assert box_position(0, 100, 640) == "左侧"
    assert box_position(220, 420, 640) == "中央"
    assert box_position(500, 640, 640) == "右侧"
    assert box_position(0, 100, 0) == "未知"
