"""Render fused mini-maps: dump points transformed by probe odometry poses and
voxel-fused, i.e. what the "built map" looks like under each accumulation
window setting.  Diagnostic visualization; not part of the production chain.

Output: 2/kissicp_probe/map_reconstruction.png (2x2 grid, top view, color=height)
"""

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "2" / "kissicp_probe"

LAYOUT = [
    ("9.14_caochangzhong2", 100, "zhong2 static — 100-frame windows (KISS-ICP drift 0.44 m)"),
    ("9.14_caochangyidong", 50, "yidong translate — 50-frame windows"),
    ("9.14_caochangzuo", 10, "zuo rotate-left — 10-frame windows"),
    ("9.14_caochangyou", 10, "you rotate-right — 10-frame windows"),
]
VOXEL = 0.2


def fused_map(name: str, window: int) -> tuple[np.ndarray, np.ndarray]:
    meta = json.loads((OUT / f"{name}.json").read_text(encoding="utf-8"))
    sizes = meta["frame_sizes"]
    bounds = np.concatenate([[0], np.cumsum(sizes)]).astype(np.int64)
    pts = np.memmap(OUT / f"{name}.bin", dtype="<f4", mode="r").reshape(-1, 4)
    traj = np.load(OUT / f"{name}_acc{window}_result.npz")
    poses = traj["trajectory"]
    yaws = traj["yaw"]
    n_windows = len(poses)
    world_chunks = []
    for i in range(n_windows):
        start, stop = i * window, min((i + 1) * window, len(sizes))
        if start >= len(sizes):
            break
        block = np.asarray(pts[bounds[start]:bounds[stop]], dtype=np.float64)
        block = block[np.isfinite(block).all(axis=1) & ((block[:, :3] ** 2).sum(axis=1) > 0)]
        if len(block) == 0:
            continue
        c, s = np.cos(yaws[i]), np.sin(yaws[i])
        R = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
        world = block[:, :3] @ R.T + poses[i]
        world_chunks.append(world.astype(np.float32))
    world = np.concatenate(world_chunks)
    key = np.floor(world[:, :3] / VOXEL).astype(np.int64)
    _, idx = np.unique(key, axis=0, return_index=True)
    world = world[np.sort(idx)]
    return world, np.arange(n_windows)


def main() -> None:
    fig, axes = plt.subplots(2, 2, figsize=(16, 16))
    for ax, (name, window, title) in zip(axes.flat, LAYOUT):
        world, _ = fused_map(name, window)
        r = np.linalg.norm(world[:, :2], axis=1)
        world = world[r <= 60.0]
        sc = ax.scatter(world[:, 0], world[:, 1], c=world[:, 2], s=0.1, cmap="turbo",
                        vmin=np.percentile(world[:, 2], 2), vmax=np.percentile(world[:, 2], 98))
        ax.set_aspect("equal")
        ax.set_title(f"{title}\nfused map: {len(world)} voxels ({VOXEL} m)", fontsize=10)
        ax.set_xlabel("x (m)")
        ax.set_ylabel("y (m)")
        ax.grid(alpha=0.3)
        fig.colorbar(sc, ax=ax, label="z (m)", shrink=0.75)
    fig.suptitle("Mini fused maps: points transformed by probe odometry poses, voxel-fused\n"
                 "ghosting/dragging reflects odometry error honestly (no ground truth involved)", fontsize=12)
    fig.tight_layout()
    fig.savefig(OUT / "map_reconstruction.png", dpi=120)
    print("saved map_reconstruction.png", flush=True)


if __name__ == "__main__":
    main()
