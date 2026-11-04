# -*- coding: utf-8 -*-
"""球门标注与检测日志对账：人工标注为真值，量化检测命中率与误检。

契约版本：annotation v1 / run row v1 / eval-v1，见 docs/GOAL_DEBUG_CONTRACT.md。
本评分器只回答「检测候选与人工标注吻合程度」，不声明语义正确性。

用法（在仓库根目录）：
  .\\web\\.venv\\Scripts\\python.exe tools\\score_goal_detection.py ^
      --run Plan\\evidence\\goal-demo\\20260917-phase4 --capture c4cc19fb-3020-4ccc-8407-1521f71b7212

输入：
  --annotations  标注 JSON 目录或文件，可重复；默认扫描（按去重优先级）：
                 Plan/evidence/goal-demo/annotations-reviewed、
                 Plan/evidence/goal-demo/annotations、web/data/annotations
  --run          run_goal_demo_offline.py 的运行目录（含 goal_run.jsonl）或 JSONL 文件
输出：
  <output>/validation.json + 控制台摘要。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EVAL_CONFIG = ROOT / "tools" / "goal_eval_config.yaml"
DEFAULT_ANNOTATION_DIRS = [ROOT / "Plan" / "evidence" / "goal-demo" / "annotations-reviewed",
                           ROOT / "Plan" / "evidence" / "goal-demo" / "annotations",
                           ROOT / "web" / "data" / "annotations"]
GOAL_SLOTS = ("left_post_base", "left_post_top", "right_post_base", "right_post_top", "crossbar_mid")


def derive_truth(points: dict) -> dict:
    """由 5 个标注点派生真值：中心/宽/高/朝向。与前端展示同一算法。"""
    left_base = points["left_post_base"]
    right_base = points["right_post_base"]
    top = max(points["left_post_top"][2], points["right_post_top"][2])
    bottom = min(left_base[2], right_base[2])
    width = math.hypot(right_base[0] - left_base[0], right_base[1] - left_base[1])
    return {
        "center_xy": [(left_base[0] + right_base[0]) / 2, (left_base[1] + right_base[1]) / 2],
        "center_z": bottom + (top - bottom) / 2,
        "width": width,
        "height": top - bottom,
        "yaw": math.atan2(right_base[1] - left_base[1], right_base[0] - left_base[0]),
    }


def label_truth(label: dict) -> dict | None:
    """把一条标注转成统一真值：优先 box（框），退回 5 点派生。"""
    box = label.get("box")
    if box:
        return {
            "center_xy": [box["center"][0], box["center"][1]],
            "center_z": box["center"][2],
            "width": box["size"][1],
            "height": box["size"][2],
            "yaw": box["yaw"],
        }
    points = label.get("points")
    if points and all(key in points for key in GOAL_SLOTS):
        return derive_truth(points)
    return None


def _source_priority(path: Path) -> int:
    text = str(path).replace("\\", "/")
    if "annotations-reviewed" in text:
        return 0
    if "evidence/goal-demo/annotations" in text:
        return 1
    return 2


def load_annotations(sources: list[Path], capture_id: str | None = None) -> list[dict]:
    """合并各标注文件；每个 label 附带所属 capture、method、review_status 与来源。

    同一 (capture_id, type, frame_range, site) 的重复标注按来源优先级去重，
    annotations-reviewed > evidence/goal-demo/annotations > web/data/annotations。
    """
    ordered: list[tuple[int, Path]] = []
    for source in sources:
        if source.is_file():
            ordered.append((_source_priority(source), source))
        elif source.is_dir():
            ordered.extend((_source_priority(path), path)
                           for path in sorted(source.glob("*.json")))
    ordered.sort(key=lambda item: item[0])

    labels: list[dict] = []
    seen: set[tuple] = set()
    for _, path in ordered:
        try:
            document = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        review_status = document.get("review_status", "UNREVIEWED_LEGACY")
        for label in document.get("labels", []):
            box = label.get("box") or {}
            points = label.get("points") or {}
            site = box.get("center") or points.get("center")
            site_key = (tuple(round(float(v), 2) for v in site[:2])
                        if site and len(site) >= 2 else None)
            key = (label.get("capture_id") or "", label.get("type"),
                   tuple(label.get("frame_range") or ()),
                   label.get("observation_unit"), label.get("cloud_id"), site_key)
            if key in seen:
                continue
            seen.add(key)
            if capture_id and label.get("capture_id") != capture_id:
                continue
            enriched = dict(label)
            enriched["method"] = document.get("method", "UNKNOWN")
            enriched["labeler"] = document.get("labeler", "")
            enriched["review_status"] = label.get("review_status", review_status)
            enriched["source"] = str(path)
            labels.append(enriched)
    return labels


def filter_reviewed_labels(labels: list[dict]) -> list[dict]:
    """--reviewed-only：未复核标注不进分母（旧默认框历史保留但不参与评价）。"""
    return [label for label in labels if label.get("review_status") == "reviewed"]


def load_run(path: Path) -> list[dict]:
    jsonl = path if path.is_file() else path / "goal_run.jsonl"
    rows = []
    with jsonl.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_eval_config(path: Path | None = None) -> dict:
    cfg = json.loads((path or DEFAULT_EVAL_CONFIG).read_text("utf-8"))
    if not isinstance(cfg, dict) or not cfg.get("version"):
        raise ValueError("评价配置缺少 version 字段")
    for key in ("measured_states", "predicted_states", "lost_states", "counted_visibility"):
        if not isinstance(cfg.get(key), list):
            raise ValueError(f"评价配置缺少列表字段 {key}")
    if cfg["hit_radius_m"] <= 0 or not 0 <= cfg["min_confidence"] <= 1:
        raise ValueError("评价配置 hit_radius_m/min_confidence 非法")
    return cfg


def eval_config_version(cfg: dict) -> str:
    body = json.dumps(cfg, ensure_ascii=True, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def _norm(value) -> str | None:
    return str(value) if value not in (None, "") else None


def _label_capture(label: dict) -> str | None:
    return _norm(label.get("capture_id"))


def _row_capture(row: dict, legacy_capture: str | None) -> str | None:
    own = _norm(row.get("capture_id"))
    if own:
        return own
    return legacy_capture  # 旧格式行：仅在命令行显式 --capture 时获得归属


def _truth_hit(center, truths, radius: float) -> bool:
    return any(math.hypot(center[0] - t["center_xy"][0], center[1] - t["center_xy"][1]) <= radius
               for t in truths)


def _positive(output: dict | None, min_confidence: float) -> list | None:
    """返回达置信度门槛的输出中心；无正输出返回 None。"""
    if not output:
        return None
    # A prediction or historical LOST box is not a current measurement even
    # when it carries a high confidence score.  Legacy rows without the field
    # retain their original scoring semantics.
    if output.get("evidence_role") not in (None, "MEASURED_CURRENT"):
        return None
    center = output.get("center")
    if not center or output.get("confidence", 0) < min_confidence:
        return None
    return center


def _bucket_metrics(frame_rows: list[dict], truths: list[dict], radius: float,
                    min_confidence: float) -> dict:
    """帧级聚合：分母是标注帧集合（每帧只计一次，重叠标注不重复）。"""
    hits, positives, errors = 0, 0, []
    for row in frame_rows:
        center = _positive(row["_output"], min_confidence)
        if center is None:
            continue
        positives += 1
        distance = min((math.hypot(center[0] - t["center_xy"][0], center[1] - t["center_xy"][1])
                        for t in truths), default=None)
        if distance is not None and distance <= radius:
            hits += 1
            errors.append(distance)
    total = len(frame_rows)
    return {
        "frames": total,
        "positive_outputs": positives,
        "hits": hits,
        "misses": total - hits,
        "precision": round(hits / positives, 3) if positives else None,
        "recall": round(hits / total, 3) if total else None,
        "miss_rate": round((total - hits) / total, 3) if total else None,
        "mean_center_error_m": round(sum(errors) / len(errors), 3) if errors else None,
        "p95_center_error_m": round(float(np.percentile(errors, 95)), 3) if errors else None,
        "max_center_error_m": round(max(errors), 3) if errors else None,
    }


def score_run(rows: list[dict], labels: list[dict], hit_radius: float | None = None,
              min_confidence: float | None = None, eval_config: dict | None = None,
              capture: str | None = None) -> dict:
    """按评价契约 v1 对账：box/点标注统一、capture/帧区间隔离、UNKNOWN 帧不入分母。

    测量层输出 = ``accumulated_best``；预测层输出 = ``goal`` 框，按状态分
    measured / predicted / lost 三桶。旧格式行（无 capture_id）仅在显式
    ``capture`` 下获得归属，否则其标注记为 capture_unattributed。
    """
    cfg = dict(eval_config or load_eval_config())
    if hit_radius is not None:
        cfg["hit_radius_m"] = hit_radius
    if min_confidence is not None:
        cfg["min_confidence"] = min_confidence
    radius = float(cfg["hit_radius_m"])
    min_conf = float(cfg["min_confidence"])
    counted_visibility = set(cfg["counted_visibility"])
    measured_states = set(cfg["measured_states"])
    predicted_states = set(cfg["predicted_states"])
    lost_states = set(cfg["lost_states"])

    rows = list(rows)
    row_captures = {_norm(row.get("capture_id")) for row in rows}
    row_captures.discard(None)
    legacy_capture = _norm(capture) if capture and not row_captures else None
    capture_attribution = ("row" if row_captures
                           else "legacy_via_cli" if legacy_capture else "unattributed")
    effective_captures = set(row_captures)
    if legacy_capture:
        effective_captures.add(legacy_capture)

    # ---- 标注整理：可评价真值 / 负样本 / 被排除标注及原因
    truths: list[dict] = []
    negative_labels: list[dict] = []
    skipped_labels: list[dict] = []
    for label in labels:
        if label.get("observation_unit") == "accumulated_window" or (
                label.get('annotation_context') or {}).get('source') == 'demo_accumulated_cloud':
            skipped_labels.append({**label, "skip_reason": "window_truth_not_single_frame"})
            continue
        if label.get("type") == "non_goal_structure":
            negative_labels.append(label)
            continue
        if label.get("type") != "goal":
            skipped_labels.append({**label, "skip_reason": "unknown_type"})
            continue
        visibility = label.get("visibility") or "visible"
        if visibility not in counted_visibility:
            skipped_labels.append({**label, "skip_reason": f"visibility:{visibility}"})
            continue
        try:
            truth = label_truth(label)
        except (KeyError, TypeError):
            truth = None
        if truth is None:
            skipped_labels.append({**label, "skip_reason": "no_truth"})
            continue
        label_capture = _label_capture(label)
        if label_capture is None:
            skipped_labels.append({**label, "skip_reason": "capture_missing"})
            continue
        if capture_attribution == "unattributed":
            skipped_labels.append({**label, "skip_reason": "capture_unattributed"})
            continue
        if label_capture not in effective_captures:
            skipped_labels.append({**label, "skip_reason": "capture_not_in_run"})
            continue
        truths.append({"label": label, "truth": truth})

    # ---- 帧分类：每帧归属 capture，并判断是否落在该 capture 的标注区间内
    intervals: dict[str, list[tuple[int, int]]] = {}
    for entry in truths:
        label = entry["label"]
        cap = _label_capture(label) or legacy_capture or ""
        lo, hi = (int(v) for v in label["frame_range"])
        intervals.setdefault(cap, []).append((lo, hi))
    for cap in intervals:
        intervals[cap].sort()

    def annotated(cap: str | None, frame_index) -> bool:
        if cap is None:
            cap = ""
        return any(lo <= frame_index <= hi for lo, hi in intervals.get(cap, ()))

    annotated_rows: list[dict] = []
    unknown_rows: list[dict] = []
    for row in rows:
        enriched = dict(row)
        enriched["_capture"] = _row_capture(row, legacy_capture)
        enriched["_output"] = row.get("accumulated_best")
        (annotated_rows if annotated(enriched["_capture"], row.get("frame_index", -1))
         else unknown_rows).append(enriched)

    # ---- 负样本中心（用于误检点备注）
    negative_centers = []
    for label in negative_labels:
        center = None
        points = label.get("points") or {}
        if points.get("center"):
            center = points["center"]
        elif label.get("box"):
            center = label["box"]["center"]
        if center:
            negative_centers.append((label, center))

    def negative_note(center) -> str | None:
        for label, nc in negative_centers:
            if math.hypot(center[0] - nc[0], center[1] - nc[1]) <= radius:
                return label.get("note", "")
        return None

    # ---- 测量层（accumulated_best）
    measurement = _bucket_metrics(annotated_rows, [e["truth"] for e in truths], radius, min_conf)

    false_sites: dict[tuple, dict] = {}
    for row in annotated_rows:
        center = _positive(row["_output"], min_conf)
        if center is None or _truth_hit(center, [e["truth"] for e in truths], radius):
            continue
        note = negative_note(center)
        key = (round(center[0]), round(center[1]))
        site = false_sites.setdefault(key, {"center": [key[0], key[1]], "frames": 0,
                                            "max_confidence": 0.0, "negative_note": note})
        site["frames"] += 1
        site["max_confidence"] = max(site["max_confidence"], row["_output"].get("confidence", 0.0))
        if note and site["negative_note"] is None:
            site["negative_note"] = note
    false_sites_list = sorted(false_sites.values(), key=lambda site: -site["frames"])

    # ---- 预测层（goal 框，按状态分桶）
    pred_buckets: dict[str, list[dict]] = {"measured": [], "predicted": [], "lost": []}
    for row in annotated_rows:
        goal = row.get("goal") or {}
        state = goal.get("state")
        if state in measured_states:
            bucket = "measured"
        elif state in predicted_states:
            bucket = "predicted"
        elif state in lost_states:
            bucket = "lost"
        else:
            continue
        enriched = dict(row)
        enriched["_output"] = goal if goal.get("center") else None
        pred_buckets[bucket].append(enriched)
    prediction = {bucket: _bucket_metrics(bucket_rows, [e["truth"] for e in truths], radius, min_conf)
                  for bucket, bucket_rows in pred_buckets.items() if bucket != "lost"}
    prediction["lost"] = {
        "frames": len(pred_buckets["lost"]),
        "boxes_returned": sum(1 for row in pred_buckets["lost"] if row["_output"]),
    }

    # ---- per-goal 表
    per_goal = []
    for entry in truths:
        label, truth = entry["label"], entry["truth"]
        cap = _label_capture(label) or legacy_capture or ""
        lo, hi = (int(v) for v in label["frame_range"])
        frames = [row for row in annotated_rows
                  if row["_capture"] == cap and lo <= row["frame_index"] <= hi]
        hits, errors, first_hit = 0, [], None
        for row in frames:
            center = _positive(row["_output"], min_conf)
            if center is None:
                continue
            distance = math.hypot(center[0] - truth["center_xy"][0], center[1] - truth["center_xy"][1])
            if distance <= radius:
                hits += 1
                errors.append(distance)
                if first_hit is None:
                    first_hit = row["frame_index"]
        per_goal.append({
            "capture_id": cap or None,
            "frame_range": [lo, hi],
            "review_status": label.get("review_status"),
            "visibility": label.get("visibility") or "visible",
            "truth": {key: (round(value, 3) if isinstance(value, float) else value)
                      for key, value in truth.items()},
            "distance_from_origin": round(math.hypot(*truth["center_xy"]), 2),
            "frames_in_range": len(frames),
            "positive_outputs": sum(1 for row in frames if _positive(row["_output"], min_conf)),
            "hit_frames": hits,
            "hit_ratio": round(hits / len(frames), 3) if frames else None,
            "first_hit_frame": first_hit,
            "mean_center_error_m": round(sum(errors) / len(errors), 3) if errors else None,
            "max_center_error_m": round(max(errors), 3) if errors else None,
        })

    unknown_outputs = sum(1 for row in unknown_rows if _positive(row["_output"], min_conf))
    unreviewed_used = sum(1 for entry in truths if entry["label"].get("review_status") != "reviewed")
    status = "EVALUATED" if measurement["frames"] else "NOT_EVALUATED"
    return {
        "generated_at": time.time(),
        "window_evaluation": score_windows(rows, labels, radius, min_conf),
        "status": status,
        "eval_config": cfg,
        "eval_config_version": eval_config_version(cfg),
        "capture_attribution": capture_attribution,
        "capture_arg": _norm(capture),
        "hit_radius_m": radius,
        "min_confidence": min_conf,
        "goal_labels": len(truths),
        "negative_labels": len(negative_labels),
        "labels_used": len(truths) + len(negative_labels),
        "labels_skipped": len(skipped_labels),
        "unreviewed_labels_used": unreviewed_used,
        "skipped_label_reasons": sorted({label["skip_reason"] for label in skipped_labels}),
        "frames_total": len(rows),
        "measurement": measurement,
        "prediction": prediction,
        "unknown_frames": len(unknown_rows),
        "unknown_frames_with_output": unknown_outputs,
        "per_goal": per_goal,
        "false_positive_sites": false_sites_list[:20],
        "false_positive_site_count": len(false_sites_list),
        "sites_matching_negatives": sum(1 for site in false_sites_list if site["negative_note"] is not None),
    }


def score_windows(rows, labels, radius=1.5, min_confidence=.5):
    """One exact cloud/window is one observation; never expand to frame truth."""
    entries = []
    for label in labels:
        if label.get('observation_unit') != 'accumulated_window':
            continue
        entry = {'cloud_id': label.get('cloud_id'), 'frame_range': label['frame_range']}
        if label.get('review_status') != 'reviewed' or label.get('visibility') not in ('visible', 'partially_occluded'):
            entries.append({**entry, 'status': 'NOT_EVALUATED', 'reason': 'unreviewed_or_not_visible'})
            continue
        matches = [r for r in rows if r.get('observation_unit') == 'accumulated_window'
                   and all(r.get(k) == label.get(k) and label.get(k) is not None for k in
                           ('capture_id', 'capture_sha256', 'cloud_id', 'coordinate_frame',
                            'frame_range', 'device_time_range'))]
        if len(matches) != 1:
            entries.append({**entry, 'status': 'NOT_EVALUATED', 'reason': 'missing_or_duplicate_window'})
            continue
        output = matches[0].get('selected_measurement')
        center = _positive(output, min_confidence)
        if label['type'] == 'non_goal_structure':
            entries.append({**entry, 'status': 'EVALUATED', 'false_positive': center is not None})
            continue
        truth = label_truth(label)
        if truth is None:
            entries.append({**entry, 'status': 'NOT_EVALUATED', 'reason': 'no_geometry_truth'})
            continue
        error = math.dist(center[:2], truth['center_xy']) if center is not None else None
        entries.append({**entry, 'status': 'EVALUATED', 'center_error_m': error,
                        'center_hit': error is not None and error <= radius,
                        'width_error_m': abs(output['size'][1]-truth['width']) if center is not None and output.get('size') else None,
                        'height_error_m': abs(output['size'][2]-truth['height']) if center is not None and output.get('size') else None})
    return {'observation_unit': 'accumulated_window', 'windows_evaluated': sum(
        e['status'] == 'EVALUATED' for e in entries), 'entries': entries}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, help="运行目录（含 goal_run.jsonl）或 JSONL 文件")
    parser.add_argument("--annotations", action="append", default=[],
                        help="标注目录或文件，可重复；默认按优先级扫描三个标准位置")
    parser.add_argument("--capture", default=None, help="只统计该 capture_id 的标注；旧格式行（无 capture_id）凭此获得归属")
    parser.add_argument("--eval-config", default=None, help="评价配置路径，默认 tools/goal_eval_config.yaml")
    parser.add_argument("--hit-radius", type=float, default=None, help="覆盖 eval_config.hit_radius_m")
    parser.add_argument("--min-confidence", type=float, default=None, help="覆盖 eval_config.min_confidence")
    parser.add_argument("--reviewed-only", action="store_true",
                        help="忽略 review_status 非 reviewed 的标注（旧默认框不进分母）")
    parser.add_argument("--output", default=None, help="输出目录，默认 <run>-validation")
    args = parser.parse_args()

    run_path = Path(args.run)
    eval_cfg = load_eval_config(Path(args.eval_config) if args.eval_config else None)
    sources = [Path(p) for p in args.annotations] or DEFAULT_ANNOTATION_DIRS
    labels = load_annotations(sources, args.capture)
    if args.reviewed_only:
        labels = filter_reviewed_labels(labels)
    rows = load_run(run_path)
    result = score_run(rows, labels, args.hit_radius, args.min_confidence,
                       eval_config=eval_cfg, capture=args.capture)

    output_dir = Path(args.output) if args.output else (
        run_path.parent / f"{run_path.name}-validation" if run_path.is_dir()
        else run_path.parent / f"{run_path.stem}-validation")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "validation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({key: result[key] for key in
                      ("status", "eval_config_version", "capture_attribution", "goal_labels",
                       "negative_labels", "labels_skipped", "unreviewed_labels_used",
                       "frames_total", "unknown_frames", "unknown_frames_with_output",
                       "false_positive_site_count", "sites_matching_negatives")},
                     ensure_ascii=False))
    measurement = result["measurement"]
    print(f"测量层: frames={measurement['frames']} hits={measurement['hits']} "
          f"precision={measurement['precision']} recall={measurement['recall']} "
          f"miss_rate={measurement['miss_rate']}")
    for bucket, metrics in result["prediction"].items():
        if bucket == "lost":
            print(f"预测层 lost: frames={metrics['frames']} boxes_returned={metrics['boxes_returned']}")
        else:
            print(f"预测层 {bucket}: frames={metrics['frames']} hits={metrics['hits']} "
                  f"precision={metrics['precision']} recall={metrics['recall']}")
    for entry in result["per_goal"]:
        print(f"球门 @ {entry['distance_from_origin']}m 帧{entry['frame_range']} "
              f"[{entry['review_status']}/{entry['visibility']}]: "
              f"hit_ratio={entry['hit_ratio']} ({entry['hit_frames']}/{entry['frames_in_range']}) "
              f"first_hit={entry['first_hit_frame']} mean_err={entry['mean_center_error_m']}m")
    for site in result["false_positive_sites"][:10]:
        tag = f" [负样本: {site['negative_note']}]" if site["negative_note"] else ""
        print(f"误检点 ({site['center'][0]},{site['center'][1]}) "
              f"frames={site['frames']} max_conf={site['max_confidence']:.2f}{tag}")
    if result["status"] == "NOT_EVALUATED":
        print("NOT_EVALUATED：没有可用的标注帧区间（无标注/可见性排除/capture 无法归属）。")
    print(f"产物目录: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
