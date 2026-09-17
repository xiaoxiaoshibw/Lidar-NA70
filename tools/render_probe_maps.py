"""Render effect images for the 2026-09-14 probe experiments.

Outputs into 2/kissicp_probe/:
  <capture>_topview.png     full-capture point cloud, top-down, colored by height
  trajectory_comparison.png KISS-ICP trajectories at different accumulation windows
                            (fake motion shrinking as the window grows)

Diagnostic visualization for the evidence file; not part of the production chain.
"""

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "2" / "kissicp_probe"
CAPS = ["9.14_caochangzhong2", "9.14_caochangzuo", "9.14_caochangyou", "9.14_caochangyidong"]
LABELS = {"9.14_caochangzhong2": "zhong2 (static)", "9.14_caochangzuo": "zuo (rotate left)",
          "9.14_caochangyou": "you (rotate right)", "9.14_caochangyidong": "yidong (translate)"}


def topview(name: str) -> None:
    meta = json.loads((OUT / f"{name}.json").read_text(encoding="utf-8"))
    sizes = meta["frame_sizes"]
    bounds = np.concatenate([[0], np.cumsum(sizes)]).astype(np.int64)
    pts = np.memmap(OUT / f"{name}.bin", dtype="<f4", mode="r").reshape(-1, 4)
    xyz = np.asarray(pts[:, :3])
    keep = np.isfinite(xyz).all(axis=1) & ((xyz ** 2).sum(axis=1) > 0)
    xyz = xyz[keep]
    r = np.linalg.norm(xyz[:, :2], axis=1)
    xyz = xyz[r <= 60.0]  # clip to the structure zone; distant returns squeeze the fan
    if len(xyz) == 0:
        return
    if len(xyz) > 1_500_000:
        xyz = xyz[np.random.default_rng(0).choice(len(xyz), 1_500_000, replace=False)]
    fig, ax = plt.subplots(figsize=(8, 8))
    sc = ax.scatter(xyz[:, 0], xyz[:, 1], c=xyz[:, 2], s=0.15, cmap="turbo",
                    vmin=np.percentile(xyz[:, 2], 2), vmax=np.percentile(xyz[:, 2], 98))
    ax.set_aspect("equal")
    ax.set_title(f"{LABELS[name]} — full capture, top view, color = height ({meta['frames']} frames)")
    ax.set_xlabel("x (m)")
    ax.set_ylabel("y (m)")
    ax.grid(alpha=0.3)
    fig.colorbar(sc, ax=ax, label="z (m)", shrink=0.8)
    fig.tight_layout()
    fig.savefig(OUT / f"{name}_topview.png", dpi=130)
    plt.close(fig)
    print(f"saved {name}_topview.png", flush=True)


def trajectory_comparison() -> None:
    layout = [
        ("9.14_caochangzhong2", [("_result", "1 frame (0.1 s)"), ("_acc10_result", "10 frames (1 s)"),
                                 ("_acc100_result", "100 frames (10 s)")]),
        ("9.14_caochangyidong", [("_result", "1 frame"), ("_acc10_result", "10 frames"),
                                 ("_acc50_result", "50 frames")]),
        ("9.14_caochangzuo", [("_result", "1 frame"), ("_acc10_result", "10 frames")]),
        ("9.14_caochangyou", [("_result", "1 frame"), ("_acc10_result", "10 frames")]),
    ]
    fig, axes = plt.subplots(2, 2, figsize=(12, 12))
    for ax, (name, variants) in zip(axes.flat, layout):
        for suffix, label in variants:
            path = OUT / f"{name}{suffix}.npz"
            if not path.exists():
                continue
            data = np.load(path)
            traj = data["trajectory"]
            ax.plot(traj[:, 0], traj[:, 1], linewidth=0.8, label=f"{label} — path {data['steps'].sum():.0f} m")
        ax.set_aspect("equal")
        ax.set_title(LABELS[name])
        ax.set_xlabel("x (m)")
        ax.set_ylabel("y (m)")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
        ax.set_title(f"{LABELS[name]}\nKISS-ICP trajectory vs accumulation window", fontsize=10)
    fig.suptitle("KISS-ICP independent probe: fake motion collapses as the registration window grows\n"
                 "(zhong2/yidong acc100 & yidong acc50 paths near zero are the honest scale)", fontsize=11)
    fig.tight_layout()
    fig.savefig(OUT / "trajectory_comparison.png", dpi=130)
    plt.close(fig)
    print("saved trajectory_comparison.png", flush=True)


if __name__ == "__main__":
    for cap in CAPS:
        topview(cap)
    trajectory_comparison()
