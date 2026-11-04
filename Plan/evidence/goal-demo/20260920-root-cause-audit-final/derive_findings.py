"""Derive report numbers and figures from frozen real-PCAP audit artifacts."""
import json
from pathlib import Path
import sys

import numpy as np
from scipy.spatial import cKDTree
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / "tools"))
from audit_goal_failure import summarize_run

audit = json.loads((HERE / "audit.json").read_text("utf-8"))
raw = np.load(HERE / "raw-roi-0-199.npz")["points"][:, :3]
edge_stats = []
for y in (-28.8, -23.8):
    region = raw[(abs(raw[:, 1] - y) < .4) & (raw[:, 0] > 93) & (raw[:, 0] < 96)]
    kept = region[region[:, 2] > .3]
    edge_stats.append({"diagnostic_y": y, "raw_points": len(region),
                       "raw_z_min": float(region[:, 2].min()),
                       "raw_z_max": float(region[:, 2].max()),
                       "raw_z_span": float(np.ptp(region[:, 2])),
                       "after_z_gate_points": len(kept),
                       "after_z_gate_span": float(np.ptp(kept[:, 2]))})

shifted = raw.copy()
shifted[:, 2] += audit["diagnostic_interventions"]["z_shift_m"]
body = shifted[(shifted[:, 2] > .3) & (shifted[:, 2] < 1.8)]
neighbours = cKDTree(body).query_ball_tree(cKDTree(body), .8)
seen, components = set(), []
for start in range(len(body)):
    if start in seen:
        continue
    queue, indices = [start], []
    seen.add(start)
    while queue:
        index = queue.pop()
        indices.append(index)
        for other in neighbours[index]:
            if other not in seen:
                seen.add(other)
                queue.append(other)
    block = body[indices]
    center = np.median(block[:, :2], axis=0)
    rms = float(np.sqrt(np.mean(np.sum((block[:, :2] - center) ** 2, axis=1))))
    components.append({"points": len(block), "center_xy": center.tolist(),
                       "xy_rms_about_median_m": rms})
components.sort(key=lambda value: -value["points"])
before = summarize_run(HERE.parent / "20260920-root-cause-baseline/goal_run.jsonl")
after = summarize_run(HERE.parent / "20260920-root-cause-ground-fix/goal_run.jsonl")
result = {"label": "REAL_PCAP_DIAGNOSTIC_NOT_GROUND_TRUTH",
          "diagnostic_neighborhoods": {"x_range": [93, 96], "y_half_width": .4},
          "edges": edge_stats, "shifted_body_components": components[:6],
          "before_counts": before["counts"], "after_counts": after["counts"],
          "before_selected_measurement_frames": before["selected_measurement_frames"],
          "after_selected_measurement_frames": after["selected_measurement_frames"]}
(HERE / "findings.json").write_text(json.dumps(result, indent=2), "utf-8")

plt.rcParams.update({"font.family": "Microsoft YaHei", "axes.unicode_minus": False})
fig, axes = plt.subplots(1, 3, figsize=(15, 4.4), constrained_layout=True)
local = raw[(raw[:, 0] > 93) & (raw[:, 0] < 96)]
for ax, cloud, title in zip(axes[:2], [local, local[local[:, 2] > .3]],
                           ["原始回波：0–199 帧", "绝对高度过滤后：Z > 0.3 米"]):
    ax.scatter(cloud[:, 1], cloud[:, 2], s=3, color="#25476a")
    ax.axhline(.3, color="#b33a33", linestyle="--")
    ax.set(xlim=(-30, -22.5), ylim=(-1.6, 1.2), xlabel="传感器 Y / 米",
           ylabel="传感器 Z / 米", title=title)
    ax.grid(alpha=.2)
windows = {(w["start"], w["end"]): w for w in audit["windows"]}
values = [windows[(64, 83)]["raw_bev_density"]["max_density"],
          windows[(64, 83)]["voxel_bev_density"]["max_density"],
          windows[(0, 199)]["raw_bev_density"]["max_density"],
          windows[(0, 199)]["voxel_bev_density"]["max_density"]]
axes[2].bar(["20帧\n原始", "20帧\n体素", "200帧\n原始", "200帧\n体素"],
            values, color=["#25476a", "#a5adb5", "#25476a", "#a5adb5"])
axes[2].axhline(.55, color="#b33a33", linestyle="--", label="BEV 密度门限 0.55")
for i, v in enumerate(values):
    axes[2].text(i, v+.02, f"{v:.3f}", ha="center")
axes[2].set(title="体素点数除以窗口帧数，密度被压低", ylabel="最大格点数 / 窗口帧数",
            ylim=(0, 1.55))
axes[2].legend(fontsize=9)
fig.suptitle("真实 PCAP 诊断：有结构回波，但高度过滤与 BEV 门限阻断检测", fontsize=14)
fig.savefig(HERE / "failure-causes.png", dpi=160)
plt.close(fig)
print(json.dumps({"edges": edge_stats, "largest_component": components[0]}, indent=2))
