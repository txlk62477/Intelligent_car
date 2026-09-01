import numpy as np

from yolo_detect.postprocess import COCO_NAMES, decode_and_nms


def output_for(boxes, class_scores):
    output = np.zeros((1, 84, len(boxes)), dtype=np.float32)
    output[0, :4, :] = np.asarray(boxes, dtype=np.float32).T
    for candidate, scores in enumerate(class_scores):
        for label, score in scores.items():
            output[0, 4 + COCO_NAMES.index(label), candidate] = score
    return output


def emitted_labels(per_class_boxes):
    return [
        COCO_NAMES[index]
        for index, boxes in enumerate(per_class_boxes)
        for _ in range(len(boxes))
    ]


def test_one_candidate_emits_only_its_highest_scoring_class():
    output = output_for(
        boxes=[(110.0, 110.0, 20.0, 20.0)],
        class_scores=[{"bottle": 0.55, "cup": 0.60}],
    )

    boxes, _scores = decode_and_nms(output, 0.25, 0.45)

    assert emitted_labels(boxes) == ["cup"]


def test_spatially_separate_boxes_are_not_suppressed_by_nms():
    output = output_for(
        boxes=[
            (110.0, 110.0, 20.0, 20.0),
            (140.0, 110.0, 20.0, 20.0),
        ],
        class_scores=[{"cup": 0.90}, {"cup": 0.80}],
    )

    boxes, _scores = decode_and_nms(output, 0.25, 0.45)

    assert emitted_labels(boxes) == ["cup", "cup"]


def test_one_region_does_not_emit_overlapping_different_names():
    output = output_for(
        boxes=[
            (110.0, 110.0, 20.0, 20.0),
            (110.0, 110.0, 20.0, 20.0),
        ],
        class_scores=[{"cup": 0.60}, {"bottle": 0.55}],
    )

    boxes, _scores = decode_and_nms(output, 0.25, 0.45)

    assert emitted_labels(boxes) == ["cup"]
