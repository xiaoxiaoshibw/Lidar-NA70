"""Scorer contract tests: truth derivation and run scoring against labels (SYNTHETIC).

All expected counts in this file are hand-computable from the fixtures; any change
to tools/goal_eval_config.yaml or score_run semantics must update the expectations
here with an explicit reason (docs/GOAL_DEBUG_CONTRACT.md).
"""
import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from score_goal_detection import (derive_truth, load_annotations, load_eval_config,
                                  score_run)  # noqa: E402

EVAL = load_eval_config()


def goal_label(center=(80.0, 6.25), frame_range=(0, 9), capture="cap", **extra):
    label = {"capture_id": capture, "type": "goal", "frame_range": list(frame_range),
             "box": {"center": [center[0], center[1], 1.3], "size": [0.6, 7.32, 2.44],
                     "yaw": 1.57}}
    label.update(extra)
    return label


def row(frame, center=None, capture="cap", confidence=0.9, goal=None):
    return {"frame_index": frame, "capture_id": capture,
            "accumulated_best": ({"center": list(center), "confidence": confidence}
                                 if center else None),
            "goal": goal or {}}


def test_derive_truth_from_five_points():
    points = {
        "left_post_base": [100.0, -3.66, 0.2],
        "left_post_top": [100.0, -3.66, 2.44],
        "right_post_base": [100.0, 3.66, 0.2],
        "right_post_top": [100.0, 3.66, 2.44],
        "crossbar_mid": [100.0, 0.0, 2.44],
    }
    truth = derive_truth(points)
    assert truth["width"] == pytest.approx(7.32, abs=0.01)
    assert truth["height"] == pytest.approx(2.24, abs=0.01)
    assert truth["center_xy"] == [100.0, 0.0]
    assert truth["center_z"] == pytest.approx(1.32, abs=0.01)
    assert truth["yaw"] == pytest.approx(math.pi / 2, abs=0.01)


def test_box_only_goal_is_scored():
    """R06 回归：UI 保存的纯框标注必须进入真值（修复前 goal_labels=0）。"""
    labels = [goal_label()]
    rows = [row(i, (80.1, 6.2)) for i in range(10)]
    result = score_run(rows, labels, eval_config=EVAL)
    assert result["goal_labels"] == 1
    assert result["status"] == "EVALUATED"
    assert result["measurement"]["hits"] == 10
    assert result["measurement"]["recall"] == 1.0
    assert result["per_goal"][0]["truth"]["width"] == pytest.approx(7.32)


def test_points_annotation_still_scored():
    labels = [{
        "capture_id": "cap", "type": "goal", "frame_range": [0, 9],
        "points": {
            "left_post_base": [80.0, 2.6, 0.2], "left_post_top": [80.0, 2.6, 2.44],
            "right_post_base": [80.0, 9.9, 0.2], "right_post_top": [80.0, 9.9, 2.44],
            "crossbar_mid": [80.0, 6.25, 2.44],
        },
    }]
    rows = [row(i, (80.1, 6.2)) for i in range(10)]
    result = score_run(rows, labels, eval_config=EVAL)
    assert result["goal_labels"] == 1
    assert result["measurement"]["hits"] == 10


def test_false_positives_only_inside_annotated_range():
    """标注区间 [0,14]：区间内非命中输出算误检；区间外帧是 UNKNOWN 不入分母。"""
    labels = [goal_label(frame_range=(0, 14)), {
        "capture_id": "cap", "type": "non_goal_structure", "frame_range": [0, 14],
        "points": {"center": [110.0, -49.0, 1.2]}, "note": "围栏线段",
    }]
    rows = [row(i, (80.1, 6.2)) for i in range(10)]              # 10 命中
    rows += [row(i, (110.2, -48.9)) for i in range(10, 15)]     # 5 帧误检（近负样本）
    rows.append(row(15, (130.0, 10.0)))                          # 区间外 → UNKNOWN
    rows.append(row(16, None))                                   # 区间外无输出

    result = score_run(rows, labels, eval_config=EVAL)
    measurement = result["measurement"]
    assert measurement["frames"] == 15           # 只有 0–14 计分
    assert measurement["hits"] == 10
    assert measurement["positive_outputs"] == 15
    assert measurement["precision"] == round(10 / 15, 3)
    assert measurement["recall"] == round(10 / 15, 3)
    assert result["unknown_frames"] == 2
    assert result["unknown_frames_with_output"] == 1
    assert result["false_positive_site_count"] == 1
    assert result["sites_matching_negatives"] == 1
    assert result["false_positive_sites"][0]["frames"] == 5


def test_cross_capture_isolation():
    """cap-b 与 cap-a 真值同坐标的输出不得计入 cap-a，也不为 cap-b 判误检。"""
    labels = [goal_label(center=(50.0, 0.0), frame_range=(0, 9), capture="cap-a")]
    rows = [row(i, (50.1, 0.0), capture="cap-a") for i in range(5)]
    rows += [row(i, None, capture="cap-a") for i in range(5, 10)]
    rows += [row(i, (50.1, 0.0), capture="cap-b") for i in range(10)]
    result = score_run(rows, labels, eval_config=EVAL)
    assert result["measurement"]["frames"] == 10      # 只有 cap-a 的帧
    assert result["measurement"]["hits"] == 5
    assert result["measurement"]["recall"] == 0.5
    assert result["unknown_frames"] == 10             # cap-b 全部未标注
    assert result["unknown_frames_with_output"] == 10
    assert result["false_positive_site_count"] == 0


def test_label_of_other_capture_not_in_run():
    """运行中不存在的 capture 的标注不得进入真值（防跨 capture 泄漏）。"""
    labels = [goal_label(center=(50.0, 0.0), frame_range=(0, 9), capture="cap-a"),
              goal_label(center=(60.0, 0.0), frame_range=(0, 9), capture="cap-z")]
    rows = [row(i, (50.1, 0.0), capture="cap-a") for i in range(10)]
    result = score_run(rows, labels, eval_config=EVAL)
    assert result["goal_labels"] == 1
    assert result["labels_skipped"] == 1
    assert result["skipped_label_reasons"] == ["capture_not_in_run"]
    assert result["measurement"]["hits"] == 10


def test_no_labels_not_evaluated():
    result = score_run([row(0, (1.0, 1.0))], [], eval_config=EVAL)
    assert result["status"] == "NOT_EVALUATED"
    assert result["goal_labels"] == 0
    assert result["measurement"]["frames"] == 0
    assert result["measurement"]["recall"] is None
    assert result["measurement"]["mean_center_error_m"] is None
    assert result["unknown_frames"] == 1


def test_visibility_invisible_excluded_from_denominator():
    labels = [goal_label(center=(70.0, 0.0), frame_range=(0, 9), visibility="invisible"),
              goal_label(center=(60.0, 0.0), frame_range=(10, 19),
                         visibility="partially_occluded")]
    rows = [row(i, (70.1, 0.0)) for i in range(10)]
    rows += [row(i, (60.1, 0.0)) for i in range(10, 20)]
    result = score_run(rows, labels, eval_config=EVAL)
    assert result["goal_labels"] == 1                  # invisible 被排除
    assert result["labels_skipped"] == 1
    assert result["skipped_label_reasons"] == ["visibility:invisible"]
    assert result["measurement"]["frames"] == 10       # 只有 10–19 计分
    assert result["measurement"]["hits"] == 10
    assert result["unknown_frames"] == 10              # 0–9 落入 UNKNOWN


def test_measurement_prediction_lost_separation():
    """测量层与预测层分开计；LOST 历史框不得记入任何准确率分母（R04 评分侧）。"""
    labels = [goal_label(center=(50.0, 0.0), frame_range=(0, 9))]
    rows = []
    for i in range(4):
        rows.append(row(i, (90.0, 90.0),
                        goal={"state": "TRACKING", "center": [50.1, 0.0, 1.3],
                              "confidence": 0.9}))
    for i in range(4, 7):
        rows.append(row(i, (90.0, 90.0),
                        goal={"state": "REACQUIRE", "center": [50.2, 0.0, 1.3],
                              "confidence": 0.9}))
    for i in range(7, 10):
        rows.append(row(i, (90.0, 90.0),
                        goal={"state": "LOST", "center": [50.0, 0.0, 1.3],
                              "confidence": 0.95}))
    result = score_run(rows, labels, eval_config=EVAL)

    measurement = result["measurement"]
    assert measurement["hits"] == 0                    # 候选全部远离真值
    assert measurement["precision"] == 0.0
    prediction = result["prediction"]
    assert prediction["measured"]["frames"] == 4 and prediction["measured"]["hits"] == 4
    assert prediction["predicted"]["frames"] == 3 and prediction["predicted"]["hits"] == 3
    assert prediction["lost"] == {"frames": 3, "boxes_returned": 3}
    assert "lost" not in prediction or prediction["lost"].get("recall") is None


def test_low_confidence_output_not_positive():
    labels = [goal_label()]
    rows = [row(i, (80.1, 6.2), confidence=0.3) for i in range(10)]
    result = score_run(rows, labels, eval_config=EVAL)
    assert result["measurement"]["positive_outputs"] == 0
    assert result["measurement"]["recall"] == 0.0
    assert result["measurement"]["precision"] is None
    assert result["false_positive_site_count"] == 0


def test_legacy_rows_require_capture_flag():
    """旧格式行（无 capture_id）：显式 --capture 才获得归属，否则标注被排除。"""
    labels = [goal_label(center=(50.0, 0.0), frame_range=(0, 4))]
    rows = [{"frame_index": i, "accumulated_best": {"center": [50.1, 0.0, 1.3],
                                                    "confidence": 0.9}} for i in range(5)]

    unattributed = score_run(rows, labels, eval_config=EVAL)
    assert unattributed["capture_attribution"] == "unattributed"
    assert unattributed["status"] == "NOT_EVALUATED"
    assert "capture_unattributed" in unattributed["skipped_label_reasons"]

    attributed = score_run(rows, labels, eval_config=EVAL, capture="cap")
    assert attributed["capture_attribution"] == "legacy_via_cli"
    assert attributed["status"] == "EVALUATED"
    assert attributed["measurement"]["hits"] == 5


def test_load_annotations_filters_capture(tmp_path):
    document = {
        "version": 1, "method": "MANUAL", "labeler": "tester",
        "labels": [
            {"capture_id": "cap-a", "type": "goal", "frame_range": [0, 1], "box": None},
            {"capture_id": "cap-b", "type": "goal", "frame_range": [0, 1], "box": None},
        ],
    }
    source = tmp_path / "cap-a.json"
    source.write_text(json.dumps(document), encoding="utf-8")
    labels = load_annotations([tmp_path], capture_id="cap-a")
    assert len(labels) == 1 and labels[0]["method"] == "MANUAL"
    assert len(load_annotations([tmp_path])) == 2


def test_load_annotations_dedupes_reviewed_over_legacy(tmp_path):
    label = {"capture_id": "cap", "type": "goal", "frame_range": [0, 9],
             "box": {"center": [0, 0, 1], "size": [0.6, 7.32, 2.44], "yaw": 0.0}}
    reviewed_dir = tmp_path / "annotations-reviewed"
    legacy_dir = tmp_path / "annotations"
    reviewed_dir.mkdir()
    legacy_dir.mkdir()
    (reviewed_dir / "cap.json").write_text(json.dumps(
        {"version": 1, "method": "MANUAL", "review_status": "PENDING_REVIEW",
         "labels": [label]}), encoding="utf-8")
    (legacy_dir / "cap.json").write_text(json.dumps(
        {"version": 1, "method": "MANUAL", "labels": [label]}), encoding="utf-8")

    labels = load_annotations([legacy_dir, reviewed_dir])
    assert len(labels) == 1
    assert labels[0]["review_status"] == "PENDING_REVIEW"


def test_load_annotations_keeps_two_goals_in_same_frame(tmp_path):
    near = goal_label(center=(94.3, -26.3), frame_range=(4, 4))
    far = goal_label(center=(92.7, 29.1), frame_range=(4, 4))
    source = tmp_path / "two-goals.json"
    source.write_text(json.dumps({"version": 2, "method": "MANUAL",
                                  "labels": [near, far]}), encoding="utf-8")
    labels = load_annotations([source], capture_id="cap")
    assert len(labels) == 2
    assert {tuple(label["box"]["center"][:2]) for label in labels} == {
        (94.3, -26.3), (92.7, 29.1)}


def test_reviewed_only_filter_excludes_unreviewed_labels():
    """--reviewed-only：旧默认框（PENDING_REVIEW）不进分母，reviewed 保留。"""
    from score_goal_detection import filter_reviewed_labels  # noqa: E402

    reviewed = goal_label(review_status="reviewed")
    pending = goal_label(frame_range=(10, 19), review_status="PENDING_REVIEW")
    unreviewed = goal_label(frame_range=(20, 29))
    kept = filter_reviewed_labels([reviewed, pending, unreviewed])
    assert kept == [reviewed]

    # 端到端语义：仅 reviewed 帧进入分母
    rows = [row(i, (80.1, 6.2)) for i in range(30)]
    result = score_run(rows, kept, eval_config=EVAL)
    assert result["status"] == "EVALUATED"
    assert result["measurement"]["frames"] == 10
    assert result["measurement"]["hits"] == 10
