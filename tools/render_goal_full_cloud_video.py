"""Render every nonzero raw return as a moving full-field BEV point cloud.

The left panel retains the full scan extent; the right panel magnifies the
football-goal area.  Both panels use only the current frame's original points.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))
from backend import decoder  # noqa: E402
from backend.goal_detection.geometry import oriented_box_corners  # noqa: E402


def _box_xy(corners):
    if not corners:
        return None
    xy = np.asarray(corners, dtype=float)[:, :2]
    unique = np.unique(np.round(xy, 3), axis=0)
    if len(unique) != 4:
        return None
    center = unique.mean(axis=0)
    order = np.argsort(np.arctan2(unique[:, 1] - center[1],
                                  unique[:, 0] - center[0]))
    result = unique[order]
    return np.vstack((result, result[0]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pcap", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--start", type=int, default=20)
    parser.add_argument("--end", type=int, default=209)
    parser.add_argument("--fps", type=int, default=10)
    parser.add_argument("--output", required=True)
    parser.add_argument("--gif")
    parser.add_argument("--preview")
    parser.add_argument("--annotations", help="reviewed window labels for this capture")
    args = parser.parse_args()

    run_dir = Path(args.run)
    rows = {}
    with (run_dir / "goal_run.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            rows[int(row["frame_index"])] = row
    reviewed_goals = {}
    reviewed_negative = None
    reviewed_end = -1
    if args.annotations:
        label_pack = json.loads(Path(args.annotations).read_text(encoding="utf-8"))
        for label in label_pack.get("labels", []):
            if label.get("review_status") != "reviewed":
                continue
            frame_range = label.get("frame_range") or []
            if len(frame_range) != 2:
                continue
            reviewed_end = max(reviewed_end, int(frame_range[1]))
            if label.get("type") == "goal" and label.get("box"):
                box = label["box"]
                key = "对面球门" if box["center"][1] > 0 else "近侧球门"
                site = reviewed_goals.setdefault(key, {"box": box, "visible": set()})
                if label.get("visibility") in ("visible", "partially_occluded"):
                    site["visible"].update(range(int(frame_range[0]), int(frame_range[1]) + 1))
            elif label.get("type") == "non_goal_structure" and label.get("box"):
                reviewed_negative = label["box"]
    pcap = Path(args.pcap)
    scan = decoder.scan_pcap(pcap)
    frames = {int(frame["index"]): frame for frame in scan.frames}
    frame_dir = Path(tempfile.mkdtemp(prefix="goal-full-cloud-"))
    rendered = 0
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    try:
        with pcap.open("rb") as source:
            for index in range(args.start, args.end + 1):
                if index not in frames:
                    continue
                raw = np.asarray(decoder.decode_frame(pcap, frames[index], source=source))
                xyz = raw[:, :3]
                radius = np.linalg.norm(xyz, axis=1)
                valid = np.isfinite(xyz).all(axis=1) & (radius > 0.1)
                xyz = xyz[valid]
                row = rows.get(index, {})
                goal = row.get("goal") or {}
                candidate = row.get("accumulated_best") or {}
                state = goal.get("state", "原始回放")
                current_measurement = bool(goal.get("has_measurement"))
                if state == "LOST":
                    track_style = ("#8a8a8a", ":", "历史位置·已失锁")
                elif state == "REACQUIRE":
                    track_style = ("#8a8a8a", "--", "搜索参考·未确认")
                elif not current_measurement:
                    track_style = ("#525252", "--", "预测框·无当前测量")
                else:
                    track_style = ("#151515", "-", "当前跟踪测量")
                fig, axes = plt.subplots(1, 3, figsize=(18.6, 6.3), dpi=105,
                                         gridspec_kw={"width_ratios": [1.1, 1.1, 1]})
                fig.patch.set_facecolor("#fcfcfb")
                for ax, bounds, title in (
                    (axes[0], (-1000, 720, -280, 250), "完整原始点集 · XY 俯视"),
                    (axes[1], (0, 400, -180, 105), "操场主体 · 同一当前帧"),
                    (axes[2], (65, 125, -45, 45), "球门区域放大 · 同一当前帧"),
                ):
                    ax.set_facecolor("#fcfcfb")
                    ax.scatter(xyz[:, 0], xyz[:, 1], s=1.3 if ax is axes[0] else 3.0,
                               c=np.clip(xyz[:, 2], -3, 6), cmap="viridis",
                               vmin=-3, vmax=6, linewidths=0, rasterized=True)
                    for box, color, style, label in (
                        (goal.get("corners"), *track_style),
                        (candidate.get("corners"), "#d97706", "--", "检测候选"),
                    ):
                        quad = _box_xy(box)
                        if quad is not None:
                            ax.plot(quad[:, 0], quad[:, 1], color=color,
                                    ls=style, lw=2.0, label=label)
                    if index <= reviewed_end:
                        for site_name, site in reviewed_goals.items():
                            box = site["box"]
                            truth_corners = oriented_box_corners(
                                box["center"], box["size"], box["yaw"])
                            quad = _box_xy(truth_corners)
                            if quad is not None:
                                visible = index in site["visible"]
                                ax.plot(quad[:, 0], quad[:, 1], color="#14803c",
                                        ls="-" if visible else ":", lw=2.5,
                                        label=f"人工复核·{site_name}" +
                                              ("" if visible else "（本帧未见）"))
                    if index <= reviewed_end and reviewed_negative is not None:
                        cx, cy = reviewed_negative["center"][:2]
                        ax.scatter([cx], [cy], marker="x", s=110,
                                   c="#dc2626", linewidths=2.2,
                                   label="人工复核·非球门")
                    ax.set_xlim(bounds[0], bounds[1])
                    ax.set_ylim(bounds[2], bounds[3])
                    ax.set_aspect("equal", adjustable="box")
                    ax.set_xlabel("x [m]")
                    ax.set_ylabel("y [m]")
                    ax.set_title(title, fontsize=11)
                    ax.grid(alpha=0.18, lw=0.5)
                    if ax is axes[2] and ax.get_legend_handles_labels()[0]:
                        ax.legend(loc="lower left", fontsize=8)
                t = row.get("device_time", frames[index].get("device_time"))
                visible_count = sum(index in site["visible"]
                                    for site in reviewed_goals.values())
                truth_state = (f"球门真值:{visible_count}处本帧可见" if index <= reviewed_end
                               else "球门真值:未标注")
                fig.suptitle(f"frame {index}  t={t:.2f}s  {state}"
                             f"  原始有效回波 {len(xyz)} 点  {truth_state}", fontsize=12)
                fig.tight_layout()
                frame_path = frame_dir / f"frame{rendered:04d}.png"
                fig.savefig(frame_path)
                if args.preview and index == (args.start + args.end) // 2:
                    shutil.copyfile(frame_path, args.preview)
                plt.close(fig)
                rendered += 1
        if rendered == 0:
            raise SystemExit("No frames rendered")
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error", "-framerate", str(args.fps),
            "-i", str(frame_dir / "frame%04d.png"),
            "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", "-crf", "22", args.output,
        ], check=True)
        if args.gif:
            from PIL import Image
            images = [Image.open(frame_dir / f"frame{i:04d}.png").convert("RGB")
                      for i in range(rendered)]
            quantized = [im.convert("P", palette=Image.ADAPTIVE, colors=128)
                         for im in images]
            quantized[0].save(args.gif, save_all=True,
                              append_images=quantized[1:],
                              duration=round(1000 / args.fps), loop=0,
                              optimize=True)
            for im in images:
                im.close()
        print(f"rendered {rendered} full-cloud frames -> {args.output}")
    finally:
        shutil.rmtree(frame_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
