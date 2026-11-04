"""Static inspection figures for W1. Sampling applies to figures only."""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator
import numpy as np


def sampled_indices(mask, budget):
    indices = np.flatnonzero(mask)
    if len(indices) > budget:
        indices = indices[np.linspace(0, len(indices) - 1, budget, dtype=int)]
    return indices


def render_window(arrays, cfg, row, packet_rows, output: Path):
    xyz = arrays["points"][:, :3]
    valid = arrays["valid_mask"]
    target = arrays["target_roi_mask"]
    background = arrays["background_roi_mask"]
    corridor = arrays["reference_corridor_mask"]
    groups = [(valid & ~target & ~background, "Other valid returns", "#9aa4b2"),
              (target & ~corridor, "Target ROI (unattributed)", "#0072b2"),
              (background, "Background ROI", "#009e73"),
              (corridor, "Reference corridor (unattributed)", "#d55e00")]
    files = []
    variants = [("overview", None)]
    if cfg["target_roi"] is not None:
        variants.append(("target-roi", [(cfg["target_roi"], "#0072b2")]))
    variants.extend((f"background-roi-{index}", [(box, "#009e73")])
                    for index, box in enumerate(cfg["background_rois"]))
    span = row["actual_span_s"]
    span_label = f"{span:.6f}" if span is not None else "unknown"
    subtitle = f"{cfg['acquisition']['data_label']} | {row['window_id']} | span={span_label} s | {row['time_quality']}"
    for variant, local_boxes in variants:
        fig, axes = plt.subplots(1, 3, figsize=(15, 5.3), layout="constrained")
        drawn = 0
        bounds = None
        if local_boxes is not None:
            boxes = np.asarray([box for box, _ in local_boxes])
            lo, hi = boxes[:, ::2].min(axis=0), boxes[:, 1::2].max(axis=0)
            margin = (hi - lo) * .05
            bounds = lo - margin, hi + margin
        active_groups = []
        for mask, label, color in groups:
            if bounds is not None:
                mask = mask & np.all((xyz >= bounds[0]) & (xyz <= bounds[1]), axis=1)
            if mask.any():
                active_groups.append((mask, label, color))
        for group_index, (mask, label, color) in enumerate(active_groups):
            group_budget = cfg["render_max_points"] // len(active_groups)
            group_budget += int(group_index < cfg["render_max_points"] % len(active_groups))
            indices = sampled_indices(mask, group_budget)
            drawn += len(indices)
            for ax, (a, b) in zip(axes, ((0, 1), (0, 2), (1, 2))):
                if len(indices):
                    ax.scatter(xyz[indices, a], xyz[indices, b], s=2, c=color, label=label, rasterized=True)
        for ax, (a, b), title in zip(axes, ((0, 1), (0, 2), (1, 2)), ("XY / top", "XZ / front", "YZ / side")):
            if cfg["target_reference"] is not None and variant in ("overview", "target-roi"):
                endpoints = np.asarray(cfg["target_reference"]["endpoints_sensor_m"])
                ax.plot(endpoints[:, a], endpoints[:, b], "k--", linewidth=1.2, label="Independent reference")
                radius = cfg["target_reference"]["corridor_radius_m"]
                ax.add_patch(plt.Rectangle((endpoints[:, a].min() - radius, endpoints[:, b].min() - radius),
                                          np.ptp(endpoints[:, a]) + 2*radius, np.ptp(endpoints[:, b]) + 2*radius,
                                          fill=False, linestyle=":", color="#d55e00", linewidth=.8,
                                          label="Reference corridor bounds"))
            boxes = local_boxes
            if boxes is None:
                boxes = ([(cfg["target_roi"], "#0072b2")] if cfg["target_roi"] is not None else [])
                boxes += [(box, "#009e73") for box in cfg["background_rois"]]
            for box, color in boxes:
                ax.add_patch(plt.Rectangle((box[2*a], box[2*b]), box[2*a+1] - box[2*a], box[2*b+1] - box[2*b], fill=False, color=color, linewidth=.8))
            if bounds is not None:
                ax.set_xlim(bounds[0][a], bounds[1][a])
                ax.set_ylim(bounds[0][b], bounds[1][b])
            ax.set_xlabel(f"{'XYZ'[a]} (m)")
            ax.set_ylabel(f"{'XYZ'[b]} (m)")
            ax.xaxis.set_major_locator(MaxNLocator(nbins=5))
            ax.yaxis.set_major_locator(MaxNLocator(nbins=5))
            ax.set_title(title)
            ax.set_aspect("equal", adjustable="box")
            ax.grid(alpha=.2)
        handles, labels = axes[0].get_legend_handles_labels()
        if handles:
            fig.legend(handles, labels, loc="outside lower center", ncol=3, fontsize=8, markerscale=3)
        fig.suptitle(subtitle + f"\n{variant}: rendered {drawn} / {int(valid.sum())} valid returns; NPZ/statistics unsampled; attribution NOT_EVALUATED", fontsize=10)
        path = output / "figures" / f"{row['window_id']}-{variant}.png"
        fig.savefig(path, dpi=cfg["render_dpi"])
        plt.close(fig)
        files.append(path.relative_to(output).as_posix())
    fig, axes = plt.subplots(2, 1, figsize=(11, 6), layout="constrained", sharex=True)
    order = np.array([p["index"] for p in packet_rows])
    times = np.array([p["device_time"] if p["device_time"] is not None else np.nan for p in packet_rows])
    finite = times[np.isfinite(times)]
    if len(finite):
        axes[0].plot(order, times - finite[0], color="#555555", linewidth=1)
    axes[0].set_ylabel("Device time since first\nvalid packet (s)")
    axes[0].grid(alpha=.2)
    for key, label, color in (("valid_mask", "All valid", "#9aa4b2"),
                              ("target_roi_mask", "Target ROI", "#0072b2"),
                              ("background_roi_mask", "Background ROI", "#009e73"),
                              ("reference_corridor_mask", "Reference corridor", "#d55e00")):
        if key == "target_roi_mask" and cfg["target_roi"] is None:
            continue
        if key == "background_roi_mask" and not cfg["background_rois"]:
            continue
        if key == "reference_corridor_mask" and cfg["target_reference"] is None:
            continue
        axes[1].plot(order, [p[key] for p in packet_rows], label=label, color=color, linewidth=1)
    axes[1].set_xlabel("NA70 packet index in capture order")
    axes[1].set_ylabel("Returns per packet")
    axes[1].grid(alpha=.2)
    axes[1].legend(loc="upper right", fontsize=8)
    fig.suptitle(subtitle + "\nPacket-level time only; counts are not confirmed wire returns", fontsize=10)
    path = output / "figures" / f"{row['window_id']}-timeline.png"
    fig.savefig(path, dpi=cfg["render_dpi"])
    plt.close(fig)
    files.append(path.relative_to(output).as_posix())
    return files
