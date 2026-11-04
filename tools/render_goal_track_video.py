"""Render a tracked-goal BEV replay video from a goal demo run.

Draws, per frame: the ROI point cloud (current frame in gray, a trailing
window in blue as the accumulation context), the tracker's box from the run
JSONL, and the best accumulated candidate when present.  Frames are rendered
with matplotlib and assembled into an MP4 with system ffmpeg.

Evidence artifacts only — the JSONL remains the numeric record.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from collections import deque
from pathlib import Path

import matplotlib
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))

from backend import decoder  # noqa: E402

import matplotlib.pyplot as plt  # noqa: E402

INK = "#1a1a19"
TEXT = "#3d3d3a"
MUTED = "#6f6e69"
GRID = "#e4e3dd"
SURFACE = "#fcfcfb"
CONTEXT = "#d9d8d2"
ACCENT = "#2a78d6"


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pcap", required=True)
    parser.add_argument("--run", required=True, help="run dir containing goal_run.jsonl")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--end", type=int, default=200)
    parser.add_argument("--window", type=int, default=10,
                        help="trailing frames shown as accumulation context")
    parser.add_argument("--fps", type=int, default=8)
    parser.add_argument("--step", type=int, default=1,
                        help="只渲染每 N 帧（上下文仍为连续帧），用于长段抽帧")
    parser.add_argument("--output", default=None, help="output MP4 path")
    parser.add_argument("--no-mp4", action="store_true", help="不合成 MP4")
    parser.add_argument("--save-frames", default=None,
                        help="把逐帧 PNG 合集保留到该目录（不删除）")
    parser.add_argument("--apng", default=None, help="额外输出动图 APNG 路径")
    parser.add_argument("--gif", default=None, help="额外输出 GIF 路径")
    return parser.parse_args()


def box_quad(corners):
    points = np.asarray([c[:2] for c in corners], dtype=np.float64)
    uniq = []
    for c in points:
        if not any(np.allclose(c, u, atol=1e-3) for u in uniq):
            uniq.append(c)
    if len(uniq) != 4:
        return None
    quad = np.asarray(uniq)
    center = quad.mean(axis=0)
    angles = np.arctan2(quad[:, 1] - center[1], quad[:, 0] - center[0])
    return quad[np.argsort(angles)]


def main():
    args = parse_args()
    run_dir = Path(args.run)
    rows = {}
    with (run_dir / "goal_run.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            rows[int(row["frame_index"])] = row
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    roi = summary["config"]["accumulation"]["roi"]
    if roi is not None:
        bounds = np.asarray(roi, dtype=np.float64).reshape(3, 2)
    else:
        # 无 ROI 运行：从轨迹中心分布推导显示窗口（全幅中 2%–98% 分位加边距）。
        centers = np.asarray([row["goal"]["center"][:2] for row in rows.values()
                              if row.get("goal", {}).get("center")], dtype=np.float64)
        if len(centers):
            lo = np.percentile(centers, 2, axis=0) - 6.0
            hi = np.percentile(centers, 98, axis=0) + 6.0
        else:
            lo, hi = np.array([40.0, -45.0]), np.array([130.0, 45.0])
        bounds = np.array([[lo[0], hi[0]], [lo[1], hi[1]], [-2.0, 6.0]])

    output = Path(args.output) if args.output else run_dir / f"track-video-{args.start}-{args.end}.mp4"
    pcap = Path(args.pcap)
    matplotlib.use("Agg")
    matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    matplotlib.rcParams["axes.unicode_minus"] = False
    scan = decoder.scan_pcap(pcap)
    frames = {int(frame["index"]): frame for frame in scan.frames}
    source = pcap.open("rb")

    frames_dir = Path(args.save_frames) if args.save_frames else None
    tmp = None
    if frames_dir is None:
        tmp = Path(tempfile.mkdtemp(prefix="goal-track-video-"))
        frames_dir = tmp
    else:
        frames_dir.mkdir(parents=True, exist_ok=True)
    try:
        history = deque(maxlen=max(args.window, 1))
        rendered = 0
        for index in range(args.start, args.end + 1):
            if index not in frames:
                continue
            points = decoder.decode_frame(pcap, frames[index], source=source)
            xyz = np.asarray(points)[:, :3]
            keep = np.all((xyz[:, :2] >= bounds[:2, 0]) & (xyz[:, :2] <= bounds[:2, 1]),
                          axis=1)
            current = xyz[keep]
            context = [block for block in history]
            history.append(current)
            if (index - args.start) % max(args.step, 1) != 0:
                continue

            row = rows.get(index, {})
            goal = row.get("goal") or {}
            state = goal.get("state", "SEARCHING")
            confidence = goal.get("confidence")
            measured = (row.get("selected_measurement") or {}).get("evidence_role")
            fig, ax = plt.subplots(figsize=(7.2, 5.4), constrained_layout=True,
                                   dpi=110)
            fig.patch.set_facecolor(SURFACE)
            ax.set_facecolor(SURFACE)
            if context:
                stacked = np.vstack(context)
                ax.scatter(stacked[:, 0], stacked[:, 1], s=2.5, c=ACCENT,
                           alpha=0.35, linewidths=0,
                           label=f"trailing {len(context)}-frame context")
            if len(current):
                ax.scatter(current[:, 0], current[:, 1], s=2.5, c=CONTEXT,
                           linewidths=0, label="current frame (ROI)")
            ax.add_patch(plt.Rectangle((bounds[0, 0], bounds[1, 0]),
                                       bounds[0, 1] - bounds[0, 0],
                                       bounds[1, 1] - bounds[1, 0],
                                       fill=False, ls=":", lw=0.8, ec=MUTED))
            quad = box_quad(goal["corners"]) if goal.get("corners") else None
            if quad is not None:
                closed = np.vstack([quad, quad[:1]])
                label = f"tracker {state}"
                if confidence is not None:
                    label += f" conf {confidence}"
                ax.plot(closed[:, 0], closed[:, 1], color=INK, lw=1.6, label=label)
            for candidate in ([row["accumulated_best"]]
                              if row.get("accumulated_best") else []):
                cand_quad = box_quad(candidate.get("corners") or [])
                if cand_quad is not None:
                    closed = np.vstack([cand_quad, cand_quad[:1]])
                    ax.plot(closed[:, 0], closed[:, 1], color=ACCENT, lw=1.0, ls="--",
                            label=f"当前帧检测测量 conf {candidate.get('confidence')}")
            device_time = row.get("device_time")
            title = (f"frame {index}" + (f"  t={device_time:.2f}s" if device_time else "")
                     + f"  {state}")
            if measured:
                title += f"  [{measured}]"
            ax.set_title(title, color=INK, fontsize=10)
            ax.set_xlabel("x [m]", color=TEXT)
            ax.set_ylabel("y [m]", color=TEXT)
            ax.set_xlim(bounds[0, 0] - 1, bounds[0, 1] + 1)
            ax.set_ylim(bounds[1, 0] - 1, bounds[1, 1] + 1)
            ax.set_aspect("equal")
            ax.grid(color=GRID, lw=0.5)
            ax.tick_params(colors=MUTED, labelsize=8)
            legend = ax.legend(loc="upper right", fontsize=7, frameon=False)
            for text in legend.get_texts():
                text.set_color(TEXT)
            fig.savefig(frames_dir / f"frame{rendered:04d}.png")
            plt.close(fig)
            rendered += 1
        produced = []
        if not args.no_mp4:
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                raise SystemExit("未找到 ffmpeg，无法合成 MP4")
            subprocess.run([ffmpeg, "-y", "-framerate", str(args.fps),
                            "-i", str(frames_dir / "frame%04d.png"),
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "23",
                            str(output)], check=True, capture_output=True)
            produced.append(str(output))
        if args.apng or args.gif:
            from PIL import Image
            images = [Image.open(frames_dir / f"frame{i:04d}.png").convert("RGB")
                      for i in range(rendered)]
            duration = int(round(1000.0 / max(args.fps, 1)))
            if args.apng:
                images[0].save(args.apng, save_all=True, append_images=images[1:],
                               duration=duration, loop=0)
                produced.append(args.apng)
            if args.gif:
                quantized = [image.convert("P", palette=Image.ADAPTIVE, colors=256)
                             for image in images]
                quantized[0].save(args.gif, save_all=True,
                                  append_images=quantized[1:], duration=duration,
                                  loop=0, optimize=True)
                produced.append(args.gif)
            for image in images:
                image.close()
        print(f"rendered {rendered} frames -> " + " | ".join(produced or [str(frames_dir)]))
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
