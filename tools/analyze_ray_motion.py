#!/usr/bin/env python3
"""Analyse an NA70 PCAP with ray-history + 4D band detection and export review files.

Outputs (all written under ``--outdir``, never into the source tree):

* ``summary.json``            parameters, band descriptors, events, counters
* ``motion_frames.csv``       per-frame metrics
* ``bands_summary.csv``       one row per accepted spatio-temporal band
* ``band_tracks.csv``         per-slice local target state (the trajectory)
* ``moving_points.ply``       accepted moving points with time and frame fields
* ``motion_timeline.png``     candidates / signal / moving points over time
* ``band_bev.png`` + ``.svg`` top-down view of the bands and their centroids
* ``bands_config.yaml``       the exact thresholds used
* ``分析方法和限制.md``         method, outputs and explicit limitations
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))
sys.path.insert(0, str(ROOT / "tools"))

from backend.decoder import decode_frame, scan_pcap  # noqa: E402
from backend.motion import ANALYSIS_VERSION, MotionConfig, analyze_capture  # noqa: E402
from png_canvas import Canvas  # noqa: E402

PALETTE = [(0xE2, 0x4B, 0x4A), (0x37, 0x8A, 0xDD), (0x63, 0x99, 0x22), (0xEF, 0x9F, 0x27),
           (0x7F, 0x77, 0xDD), (0x1D, 0x9E, 0x75), (0xD4, 0x53, 0x7E), (0x88, 0x87, 0x80)]

FRAME_COLUMNS = ["frame", "time_s", "valid_points", "ray_candidates", "clustered_signal",
                 "moving_points", "has_motion", "moving_targets", "active_bands", "candidate_ratio"]

BAND_COLUMNS = ["band_id", "label", "score", "point_count", "time_start_s", "time_end_s", "duration_s",
                "displacement_m", "trajectory_length_m", "speed_mps", "path_speed_mps", "straightness",
                "linearity", "linearity_branches", "branches", "regression_rms_m", "regression_rms_split_m",
                "coverage", "slice_count", "local_cross_norm_m", "spatial_span_m",
                "centroid_x", "centroid_y", "centroid_z"]

TRACK_COLUMNS = ["band_id", "slice_index", "time_s", "x", "y", "z", "slice_points",
                 "duration_s", "displacement_m", "trajectory_length_m"]


def write_csv(path: Path, columns: list[str], rows) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as output:
        writer = csv.writer(output)
        writer.writerow(columns)
        writer.writerows(rows)


def dump_yaml(path: Path, data: dict) -> None:
    """Minimal nested YAML emitter; the payload is scalars and nested mappings only."""
    lines: list[str] = []

    def emit(mapping: dict, indent: int) -> None:
        for key, value in mapping.items():
            pad = " " * indent
            if isinstance(value, dict):
                lines.append(f"{pad}{key}:")
                emit(value, indent + 2)
            elif isinstance(value, bool):
                lines.append(f"{pad}{key}: {'true' if value else 'false'}")
            else:
                lines.append(f"{pad}{key}: {value}")

    emit(data, 0)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_ply(path: Path, capture: Path, frames: list[dict], records: list[dict], mask_path: Path) -> int:
    selected = []
    with mask_path.open("rb") as masks:
        for frame, record in zip(frames, records):
            masks.seek(record["mask_offset"])
            mask = np.frombuffer(masks.read(record["point_count"]), dtype=np.uint8).astype(bool)
            if not mask.any():
                continue
            points = decode_frame(capture, frame)[mask]
            block = np.empty(len(points), dtype=[("x", "<f4"), ("y", "<f4"), ("z", "<f4"),
                                                  ("intensity", "<f4"), ("time", "<f4"), ("frame", "<i4")])
            for name, column in zip(("x", "y", "z", "intensity"), range(4)):
                block[name] = points[:, column]
            block["time"] = record["time"]
            block["frame"] = record["index"]
            selected.append(block)
    points = np.concatenate(selected) if selected else np.empty(
        0, dtype=[("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("intensity", "<f4"), ("time", "<f4"), ("frame", "<i4")])
    header = ("ply\nformat binary_little_endian 1.0\n"
              f"element vertex {len(points)}\n"
              "property float x\nproperty float y\nproperty float z\nproperty float intensity\n"
              "property float time\nproperty int frame\nend_header\n").encode("ascii")
    with path.open("wb") as output:
        output.write(header)
        output.write(points.tobytes())
    return len(points)


def moving_cloud(capture: Path, frames: list[dict], records: list[dict],
                 mask_path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Return (xyz, frame_index) for every point inside an accepted band."""
    blocks, frame_ids = [], []
    with mask_path.open("rb") as masks:
        for frame, record in zip(frames, records):
            masks.seek(record["mask_offset"])
            mask = np.frombuffer(masks.read(record["point_count"]), dtype=np.uint8).astype(bool)
            if not mask.any():
                continue
            points = decode_frame(capture, frame)[mask, :3]
            blocks.append(points)
            frame_ids.append(np.full(len(points), record["index"], np.int32))
    if not blocks:
        return np.empty((0, 3), np.float32), np.empty(0, np.int32)
    return np.concatenate(blocks), np.concatenate(frame_ids)


def plot_timeline(path: Path, records: list[dict], events: list[dict], title: str) -> None:
    width, height = 1280, 520
    left, right, top, bottom = 90, 40, 60, 70
    canvas = Canvas(width, height, (255, 255, 255))
    ink, grid = (0x2C, 0x2C, 0x2A), (0xDD, 0xDB, 0xD2)
    plot_w, plot_h = width - left - right, height - top - bottom
    times = np.asarray([record["time"] for record in records]) if records else np.zeros(1)
    series = [("RAY CANDIDATES", [record["candidate_count"] for record in records], (0x78, 0x90, 0x9C)),
              ("CLUSTERED SIGNAL", [record["signal_count"] for record in records], (0xEF, 0x6C, 0x00)),
              ("ACCEPTED MOVING POINTS", [record["moving_count"] for record in records], (0xD3, 0x2F, 0x2F))]
    ceiling = max([max(values, default=0) for _, values, _ in series] + [1])

    def to_x(moment: float) -> float:
        span = max(float(times[-1]) - float(times[0]), 1e-6)
        return left + plot_w * (moment - float(times[0])) / span

    def to_y(value: float) -> float:
        return top + plot_h - plot_h * value / ceiling

    for step in range(5):
        value = ceiling * step / 4
        y = to_y(value)
        canvas.line(left, y, left + plot_w, y, grid)
        canvas.text(left - 8 - canvas.text_width(f"{value:.0f}"), y - 3, f"{value:.0f}", ink)
    for event in events:
        x0, x1 = to_x(event["start_time"]), to_x(event["end_time"])
        canvas.fill_rect(x0, top, max(x1 - x0, 1), plot_h, (0xD3, 0x2F, 0x2F), alpha=0.10)
    for _, values, colour in series:
        canvas.polyline([(to_x(float(times[i])), to_y(values[i])) for i in range(len(values))], colour, 2)
    canvas.line(left, top, left, top + plot_h, ink)
    canvas.line(left, top + plot_h, left + plot_w, top + plot_h, ink)
    canvas.text(left, 20, title, ink, 2)
    canvas.text(left, top + plot_h + 34, "TIME (S)", ink)
    ticks = 6
    for step in range(ticks + 1):
        moment = float(times[0]) + (float(times[-1]) - float(times[0])) * step / ticks
        x = to_x(moment)
        canvas.line(x, top + plot_h, x, top + plot_h + 5, ink)
        canvas.text(x - canvas.text_width(f"{moment:.0f}") / 2, top + plot_h + 14, f"{moment:.0f}", ink)
    for index, (name, values, colour) in enumerate(series):
        y = 20 + index * 16
        canvas.fill_rect(left + plot_w - 360, y, 18, 8, colour)
        canvas.text(left + plot_w - 336, y - 1, f"{name}  MAX {max(values, default=0)}", ink)
    canvas.save(path)


def plot_bev(path_png: Path, path_svg: Path, cloud: np.ndarray, bands: list[dict], title: str) -> None:
    if len(cloud) == 0 and not bands:
        return
    width, height = 900, 940
    margin = 70
    canvas = Canvas(width, height, (255, 255, 255))
    ink, faint = (0x2C, 0x2C, 0x2A), (0xC9, 0xC7, 0xBE)
    points = cloud if len(cloud) else np.array([[0.0, 0.0, 0.0]])
    centres = np.array([band["centroid"] for band in bands]) if bands else np.zeros((1, 3))
    combined = np.vstack([points[:, :2], centres[:, :2]])
    low = combined.min(axis=0) - 1.0
    high = combined.max(axis=0) + 1.0
    span = max(float((high - low).max()), 1.0)

    def to_px(x: float, y: float) -> tuple[float, float]:
        return (margin + (width - 2 * margin) * (x - low[0]) / span,
                height - margin - (height - 2 * margin) * (y - low[1]) / span)

    for step in range(11):
        position = step / 10
        x = margin + (width - 2 * margin) * position
        y = height - margin - (height - 2 * margin) * position
        canvas.line(x, margin, x, height - margin, faint)
        canvas.line(margin, y, width - margin, y, faint)
    stride = max(1, len(points) // 40000)
    for x, y, _ in points[::stride]:
        px, py = to_px(float(x), float(y))
        canvas.dot(px, py, (0xB4, 0xB2, 0xA9), 0)
    for index, band in enumerate(bands):
        colour = PALETTE[index % len(PALETTE)]
        track = [to_px(centre[0], centre[1]) for centre in (state["centre"] for state in band["slices"])]
        if len(track) > 1:
            canvas.polyline(track, colour, 2)
        for position in track:
            canvas.dot(position[0], position[1], colour, 3)
        start = to_px(band["start"][0], band["start"][1])
        canvas.rect(start[0] - 6, start[1] - 6, 12, 12, colour, 2)
        canvas.text(start[0] + 10, start[1] - 6, band["label"], colour)
    canvas.line(margin, height - margin, width - margin, height - margin, ink)
    canvas.line(margin, margin, margin, height - margin, ink)
    canvas.text(margin, 24, title, ink, 2)
    canvas.text(width - margin - canvas.text_width("X (M)"), height - margin + 20, "X (M)", ink)
    for index, band in enumerate(bands[:8]):
        colour = PALETTE[index % len(PALETTE)]
        y = 60 + index * 18
        canvas.fill_rect(margin, y, 14, 10, colour)
        canvas.text(margin + 20, y + 1,
                    f"{band['label']} {band['duration']:.1f}S D={band['displacement']:.1f}M "
                    f"V={band['speed']:.2f}M/S X={band['local_cross_norm']:.2f}M", ink)
    canvas.save(path_png)

    # SVG twin: same geometry, but the browser supplies real fonts for Chinese text.
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">',
           f'<rect width="{width}" height="{height}" fill="#ffffff"/>']
    for step in range(11):
        position = step / 10
        x = margin + (width - 2 * margin) * position
        y = height - margin - (height - 2 * margin) * position
        svg.append(f'<line x1="{x:.1f}" y1="{margin}" x2="{x:.1f}" y2="{height - margin}" stroke="#c9c7be" stroke-width="1"/>')
        svg.append(f'<line x1="{margin}" y1="{y:.1f}" x2="{width - margin}" y2="{y:.1f}" stroke="#c9c7be" stroke-width="1"/>')
    for x, y, _ in points[::stride]:
        px, py = to_px(float(x), float(y))
        svg.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="0.7" fill="#b4b2a9"/>')
    for index, band in enumerate(bands):
        colour = "#%02x%02x%02x" % PALETTE[index % len(PALETTE)]
        track = [to_px(centre[0], centre[1]) for centre in (state["centre"] for state in band["slices"])]
        if len(track) > 1:
            svg.append('<polyline fill="none" stroke="%s" stroke-width="2" points="%s"/>'
                       % (colour, " ".join(f"{x:.1f},{y:.1f}" for x, y in track)))
        for x, y in track:
            svg.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{colour}"/>')
        start = to_px(band["start"][0], band["start"][1])
        svg.append(f'<rect x="{start[0] - 6:.1f}" y="{start[1] - 6:.1f}" width="12" height="12" '
                   f'fill="none" stroke="{colour}" stroke-width="2"/>')
        svg.append(f'<text x="{start[0] + 10:.1f}" y="{start[1] - 4:.1f}" font-size="13" fill="{colour}">{band["label"]}</text>')
    svg.append(f'<text x="{margin}" y="30" font-size="18" fill="#2c2c2a">{title}（俯视 X-Y）</text>')
    svg.append(f'<text x="{margin}" y="48" font-size="12" fill="#888780">点带数量为时空片段数量，不等于人数；点带长度为观测期运动路径，不是人体尺寸</text>')
    for index, band in enumerate(bands[:8]):
        colour = "#%02x%02x%02x" % PALETTE[index % len(PALETTE)]
        y = 70 + index * 20
        svg.append(f'<rect x="{margin}" y="{y}" width="14" height="10" fill="{colour}"/>')
        svg.append(f'<text x="{margin + 22}" y="{y + 11}" font-size="13" fill="#2c2c2a">'
                   f'{band["label"]} 持续 {band["duration"]:.1f} s · 位移 {band["displacement"]:.1f} m · '
                   f'速度 {band["speed"]:.2f} m/s · 截面 {band["local_cross_norm"]:.2f} m</text>')
    svg.append(f'<text x="{width - margin - 40}" y="{height - margin + 24}" font-size="13" fill="#2c2c2a">X (m)</text>')
    svg.append("</svg>")
    path_svg.write_text("\n".join(svg), encoding="utf-8")


def assign_bands(cloud: np.ndarray, frames_of: np.ndarray, records: list[dict]) -> np.ndarray:
    """Attribute each moving point to the per-frame target whose box is nearest.

    Used only for visualisation; the authoritative point-to-band mapping already
    happened inside the analyser.
    """
    labels = np.full(len(cloud), -1, np.int32)
    per_frame: dict[int, list[dict]] = {record["index"]: record["targets"] for record in records}
    for frame_index in (np.unique(frames_of) if len(frames_of) else []):
        targets = per_frame.get(int(frame_index), [])
        if not targets:
            continue
        rows = np.flatnonzero(frames_of == frame_index)
        centres = np.array([target["position"] for target in targets])
        ids = np.array([target["band_id"] for target in targets])
        distances = np.linalg.norm(cloud[rows][:, None, :] - centres[None, :, :], axis=2)
        nearest = np.argmin(distances, axis=1)
        close = distances[np.arange(len(rows)), nearest] <= 2.0
        labels[rows[close]] = ids[nearest[close]]
    return labels


def plot_band_panels(path: Path, cloud: np.ndarray, frames_of: np.ndarray, records: list[dict],
                     bands: list[dict], title: str) -> None:
    """One zoomed panel per band: shows the elongated path next to a body-sized box."""
    if not bands or len(cloud) == 0:
        return
    labels = assign_bands(cloud, frames_of, records)
    shown = bands[:6]
    columns, rows = 2, 3
    panel_w, panel_h = 640, 300
    margin, gap = 40, 24
    width = margin * 2 + panel_w * columns + gap * (columns - 1)
    height = margin * 2 + 46 + panel_h * rows + gap * (rows - 1)
    canvas = Canvas(width, height, (255, 255, 255))
    ink, faint, point_colour = (0x2C, 0x2C, 0x2A), (0xDD, 0xDB, 0xD2), (0xB4, 0xB2, 0xA9)
    canvas.text(margin, 20, title, ink, 2)
    canvas.text(margin, 42, "EACH PANEL: BAND POINTS (GREY), SLICE CENTROIDS (COLOUR), LAST SLICE BOX (BOX)", ink)

    box_by_band: dict[int, tuple] = {}
    for record in records:
        for target in record["targets"]:
            box_by_band[target["band_id"]] = (target["minimum"], target["maximum"])

    for index, band in enumerate(shown):
        column, row = index % columns, index // columns
        left = margin + column * (panel_w + gap)
        top = 70 + row * (panel_h + gap)
        colour = PALETTE[band["band_id"] % len(PALETTE)]
        canvas.rect(left, top, panel_w - 1, panel_h - 1, faint)
        selected = labels == band["band_id"]
        track = np.array([state["centre"] for state in band["slices"]])
        points = cloud[selected][:, :2] if selected.any() else np.empty((0, 2))
        combined = np.vstack([points, track[:, :2]]) if len(points) or len(track) else np.zeros((1, 2))
        low = combined.min(axis=0) - 0.6
        high = combined.max(axis=0) + 0.6
        span_x = max(float(high[0] - low[0]), 0.5)
        span_y = max(float(high[1] - low[1]), 0.5)
        # Isotropic: the same metres-per-pixel on both axes, so the band shape is
        # not distorted into looking either a circle or a line.
        scale = min((panel_w - 40) / span_x, (panel_h - 70) / span_y)
        origin_x = left + panel_w / 2 - (low[0] + high[0]) / 2 * scale
        origin_y = top + panel_h / 2 + 14 + (low[1] + high[1]) / 2 * scale

        def project(x: float, y: float) -> tuple[float, float]:
            return (origin_x + x * scale, origin_y - y * scale)

        for x, y in points:
            px, py = project(float(x), float(y))
            canvas.dot(px, py, point_colour, 1)
        if len(track) > 1:
            canvas.polyline([project(item[0], item[1]) for item in track], colour, 2)
        for item in track:
            px, py = project(item[0], item[1])
            canvas.dot(px, py, colour, 2)
        if band["band_id"] in box_by_band:
            low_box, high_box = box_by_band[band["band_id"]]
            x0, y0 = project(low_box[0], low_box[1])
            x1, y1 = project(high_box[0], high_box[1])
            canvas.rect(min(x0, x1), min(y0, y1), max(abs(x1 - x0), 3), max(abs(y1 - y0), 3), (0x2C, 0x2C, 0x2A), 2)
        canvas.text(left + 10, top + 8,
                    f"{band['label']}  {band['time_start']:.1f}-{band['time_end']:.1f}S  "
                    f"PATH {band['trajectory_length']:.2f}M  DISP {band['displacement']:.2f}M  "
                    f"V {band['speed']:.2f}M/S  BODY {band['local_cross_norm']:.2f}M", ink)
    canvas.save(path)


def method_report(path: Path, capture: Path, digest: str, summary: dict, points: int, events: list[dict]) -> None:
    diagnostics = summary["band_diagnostics"]
    bands = summary["bands"]
    lines = [
        "# 移动点带分析方法与限制",
        "",
        f"- 输入：`{capture}`",
        f"- SHA-256：`{digest}`",
        f"- 分析版本：`{ANALYSIS_VERSION}`；点带版本：`{summary['band_version']}`",
        f"- 帧数：{summary['frame_count']}；稳定射线：{summary['stable_ray_count']:,}；候选变化点：{summary['candidate_points']:,}",
        f"- 移动点：{summary['moving_points']:,}；导出点：{points:,}；运动帧：{summary['moving_frames']}",
        f"- 四维簇 {diagnostics['raw_clusters']} 个；接受 {diagnostics['accepted_bands']} 个；拒绝 {diagnostics['rejected_bands']} 个；"
        f"合并片段 {diagnostics['merged_fragments']} 次",
        "",
        "## 方法",
        "",
        "1. **射线历史背景**：用原始俯仰码与航向码构造扫描射线标识，统计同一射线上的历史距离众数；"
        "只有再次扫到同一方向且回波明显更近的点才作为候选变化点。NA70 每 100 包组成的帧不重复覆盖同一空间区域，"
        "因此普通相邻帧体素差分会把正常扫描轨迹变化误判为前景，本方法不采用它作为主算法。",
        "2. **四维时空聚类**：候选点保留 x、y、z、原始距离、intensity、frame_index、capture_time、device_time。"
        "空间按体素、时间按体素分别量化，时间轴用可配置速度尺度换算为米后与空间轴联合，"
        "对**唯一占用体素**做加权 DBSCAN（权重为体素内原始点数），避免对百万级原始点做两两距离计算。",
        "3. **点带判别**：对每个四维簇计算持续时间、时间片覆盖、起终点位移、轨迹长度、速度、"
        "PCA 主轴比、时间-位置回归残差，以及拐弯时的双分支残差。全部满足阈值才判为移动点带。"
        "拐弯路径按双分支评估，否则正常人转身会被错误拒绝。",
        "4. **瞬时目标恢复**：按时间片求局部中心并平滑，得到每一时刻的位置、速度与局部包围盒；"
        "空间相邻、时间连续、方向一致的片段做全局合并，避免一个人被拆成大量轨迹片段。",
        "5. **人体尺寸估计**：目标框取当前时间片的局部包围盒；人体宽高由**垂直于运动方向**的截面估计"
        "（`local_cross_norm`），以消除切片内运动造成的拉伸。点带长度只表示运动路径。",
        "",
        "## 输出",
        "",
        "| 文件 | 内容 |",
        "|---|---|",
        "| `summary.json` | 参数、点带描述、事件、计数与诊断 |",
        "| `motion_frames.csv` | 逐帧指标（候选点、聚类信号、移动点、目标数） |",
        "| `bands_summary.csv` | 每个点带一行的统计量 |",
        "| `band_tracks.csv` | 每个点带逐时间片的局部目标状态（轨迹） |",
        "| `moving_points.ply` | 移动点云（XYZ + 反射率 + 时间 + 帧号） |",
        "| `motion_timeline.png` | 候选点 / 聚类信号 / 移动点随时间变化 |",
        "| `band_bev.png` / `band_bev.svg` | 点带俯视可视化 |",
        "| `band_panels.png` | 逐点带放大：点带路径、时间片中心与当前时间片目标框 |",
        "| `bands_config.yaml` | 本次使用的全部阈值 |",
        "",
        "## 事件",
        "",
    ]
    if events:
        for event in events:
            lines.append(f"- 帧 {event['start_frame']}–{event['end_frame']}，"
                         f"{event['start_time']:.2f}–{event['end_time']:.2f} s，峰值移动点 {event['peak_moving_points']}")
    else:
        lines.append("- 未检测到满足门限的运动事件")
    lines += ["", "## 点带", ""]
    if bands:
        lines.append("| 点带 | 持续 s | 位移 m | 轨迹长 m | 速度 m/s | 线性度(分支) | 分支数 | 覆盖 | 截面 m | 点数 |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|")
        for band in bands:
            lines.append(f"| {band['label']} | {band['duration']:.2f} | {band['displacement']:.2f} | "
                         f"{band['trajectory_length']:.2f} | {band['speed']:.2f} | {band['linearity_branches']:.2f} | "
                         f"{band['branches']} | {band['coverage']:.2f} | {band['local_cross_norm']:.2f} | {band['point_count']} |")
    else:
        lines.append("未接受任何四维点带。")
    lines += [
        "",
        "## 限制（必须与结论一起引用）",
        "",
        "1. **点带数量不等于人数。** 点带是观测期内的时空片段；同一人多次进出、长时间遮挡，"
        "或两个目标在同一时刻占据同一四维体素，都会改变点带数量。本工具与 WebUI 均不输出“检测到 N 个人”。",
        "2. **点带长度是运动路径，不是人体尺寸。** 人体宽高由垂直于运动方向的截面估计（`local_cross_norm`），"
        "该截面仍受点云稀疏度和姿态影响。",
        "3. **四维重合无法分离。** 两个目标在同一时刻、同一体素内会被合并成一条点带，这是当前方法的结构性限制。",
        "4. **没有任何逐点真值或同步视频标注。** 本结果只能说明工程链路产生了与行人运动一致的结构，"
        "不能作为准确率或召回率结论。",
        "5. 参数为默认值，尚未在真实人群数据上调参验收；`Plan/04_验收矩阵与任务看板.md` 中相关门禁保持 `PARTIAL`。",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pcap", type=Path)
    parser.add_argument("--outdir", type=Path)
    args = parser.parse_args(argv)
    capture = args.pcap.resolve()
    output = (args.outdir or capture.with_name(capture.stem + "_bands_analysis")).resolve()
    output.mkdir(parents=True, exist_ok=True)

    scan = scan_pcap(capture)
    summary = analyze_capture(capture, scan.frames, output, MotionConfig())
    records = summary["frames"]
    bands = summary["bands"]
    events = summary["events"]

    write_csv(output / "motion_frames.csv", FRAME_COLUMNS,
              [[record["index"], f'{record["time"]:.6f}', record["considered_count"],
                record["candidate_count"], record["signal_count"], record["moving_count"],
                int(record["has_motion"]), len(record["targets"]), record.get("moving_bands", 0),
                f'{record["moving_count"] / max(record["considered_count"], 1):.6f}'] for record in records])

    write_csv(output / "bands_summary.csv", BAND_COLUMNS,
              [[band["band_id"], band["label"], f'{band["score"]:.4f}', band["point_count"],
                f'{band["time_start"]:.4f}', f'{band["time_end"]:.4f}', f'{band["duration"]:.4f}',
                f'{band["displacement"]:.4f}', f'{band["trajectory_length"]:.4f}', f'{band["speed"]:.4f}',
                f'{band["path_speed"]:.4f}', f'{band["straightness"]:.4f}',
                f'{band["linearity"]:.4f}', f'{band["linearity_branches"]:.4f}',
                band["branches"], f'{band["regression_rms"]:.4f}', f'{band["regression_rms_split"]:.4f}',
                f'{band["coverage"]:.4f}', band["slice_count"], f'{band["local_cross_norm"]:.4f}',
                f'{band["spatial_span"]:.4f}', f'{band["centroid"][0]:.4f}', f'{band["centroid"][1]:.4f}',
                f'{band["centroid"][2]:.4f}'] for band in bands])

    write_csv(output / "band_tracks.csv", TRACK_COLUMNS,
              [[band["band_id"], state["bucket"], f'{state["time"]:.4f}',
                f'{state["centre"][0]:.4f}', f'{state["centre"][1]:.4f}', f'{state["centre"][2]:.4f}',
                state["count"], f'{band["duration"]:.4f}', f'{band["displacement"]:.4f}',
                f'{band["trajectory_length"]:.4f}'] for band in bands for state in band["slices"]])

    point_count = write_ply(output / "moving_points.ply", capture, scan.frames, records,
                            output / "analysis_masks.bin")
    cloud, frames_of = moving_cloud(capture, scan.frames, records, output / "analysis_masks.bin")
    plot_timeline(output / "motion_timeline.png", records, events,
                  f"NA70 RAY-HISTORY + 4D BAND MOTION  {capture.name}")
    plot_bev(output / "band_bev.png", output / "band_bev.svg", cloud, bands,
             f"NA70 移动点带  {capture.name}")
    plot_band_panels(output / "band_panels.png", cloud, frames_of, records, bands,
                     f"NA70 BAND DETAIL  {capture.name}")
    dump_yaml(output / "bands_config.yaml", json.loads(json.dumps(summary["config"])))

    digest = hashlib.sha256(capture.read_bytes()).hexdigest()
    report = {key: value for key, value in summary.items() if key != "frames"}
    report.update(input=str(capture), sha256=digest, exported_moving_points=point_count,
                  moving_point_frames=int(len(np.unique(frames_of))) if len(frames_of) else 0,
                  outputs=sorted(item.name for item in output.iterdir()), banner="SYNTHETIC-LOGIC-ONLY",
                  note=("band count counts spatio-temporal fragments, not people; band length is the "
                        "travelled path during observation, not body size"))
    (output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    method_report(output / "分析方法和限制.md", capture, digest, summary, point_count, events)

    print(json.dumps({key: report[key] for key in
                      ("input", "frame_count", "stable_ray_count", "candidate_points", "moving_points",
                       "moving_frames", "band_count", "exported_moving_points", "elapsed_seconds")},
                     ensure_ascii=False, indent=2))
    print(f"\nOUTPUT {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
