"""Stitch the three playground fans (zhong2 / zuo / you) into one map.

Assumption to verify (from the user's recording description): the radar stayed
at one position and only rotated, so each file differs from zhong2 by (mainly)
a yaw offset plus a small translation.  For each side file we grid-search the
yaw offset that minimizes the robust nearest-neighbour distance of its
NON-GROUND points (trees, stands, rails) against the zhong2 reference, then
refine yaw+translation locally.  Rotation-only vs refined scores are reported
so the fixed-position assumption can be checked.

Diagnostic stitch, NOT the production cross-section registration demanded by
the MASTER plan (D4); no ground truth available.
"""

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.spatial import cKDTree

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "2" / "kissicp_probe"
VOXEL = 0.25


def load_cloud(name: str, max_points: int = 400_000) -> np.ndarray:
    meta = json.loads((OUT / f"{name}.json").read_text(encoding="utf-8"))
    sizes = meta["frame_sizes"]
    bounds = np.concatenate([[0], np.cumsum(sizes)]).astype(np.int64)
    pts = np.memmap(OUT / f"{name}.bin", dtype="<f4", mode="r").reshape(-1, 4)
    xyz = np.asarray(pts[:, :3], dtype=np.float64)
    xyz = xyz[np.isfinite(xyz).all(axis=1) & ((xyz ** 2).sum(axis=1) > 0)]
    key = np.floor(xyz / VOXEL).astype(np.int64)
    _, idx = np.unique(key, axis=0, return_index=True)
    xyz = xyz[np.sort(idx)]
    r = np.linalg.norm(xyz[:, :2], axis=1)
    xyz = xyz[(r > 2.0) & (r <= 60.0)]
    if len(xyz) > max_points:
        xyz = xyz[np.random.default_rng(0).choice(len(xyz), max_points, replace=False)]
    return xyz


def rot(xy: np.ndarray, deg: float) -> np.ndarray:
    t = np.radians(deg)
    c, s = np.cos(t), np.sin(t)
    return xy @ np.array([[c, -s], [s, c]]).T


def robust_score(src_xy: np.ndarray, tree: cKDTree, sample: np.ndarray) -> float:
    d, _ = tree.query(sample @ np.eye(2) if False else sample)
    return float(np.median(d))


def align(target: np.ndarray, reference: np.ndarray, name: str) -> dict:
    ref_tree = cKDTree(reference[:, :2])
    rng = np.random.default_rng(0)

    def score(xy: np.ndarray) -> float:
        sample = xy[rng.choice(len(xy), min(20000, len(xy)), replace=False)]
        d, _ = ref_tree.query(sample)
        return float(np.median(d))

    # Coarse yaw search on non-ground structures (these carry the alignment signal).
    ng = target[target[:, 2] > np.median(target[:, 2]) + 0.5][:, :2]
    if len(ng) < 500:
        ng = target[:, :2]
    best_yaw, best = 0.0, None
    for yaw in np.arange(-80.0, 80.01, 0.5):
        s = score(rot(ng, yaw))
        if best is None or s < best:
            best, best_yaw = s, yaw
    rot_only = best
    # Local refine yaw + small translation (radar assumed near-stationary).
    yaw, tx, ty = best_yaw, 0.0, 0.0
    for step in (2.0, 0.5, 0.1, 0.02):
        improved = True
        while improved:
            improved = False
            for dyaw, dx, dyy in ((step, 0, 0), (-step, 0, 0), (0, step, 0), (0, -step, 0),
                                  (0, 0, step), (0, 0, -step)):
                cand = rot(ng, yaw + dyaw) + (tx + dx, ty + dyy)
                sample = cand[rng.choice(len(cand), min(20000, len(cand)), replace=False)]
                s = float(np.median(ref_tree.query(sample)[0]))
                if s < best - 1e-4:
                    best, yaw, tx, ty = s, yaw + dyaw, tx + dx, ty + dyy
                    improved = True
    full = rot(target[:, :2], yaw) + (tx, ty)
    d, _ = ref_tree.query(full[rng.choice(len(full), min(40000, len(full)), replace=False)])
    result = dict(
        target=name, yaw_offset_deg=round(float(yaw), 2),
        translation_xy_m=[round(tx, 2), round(ty, 2)],
        rot_only_score_m=round(rot_only, 3),
        refined_score_m=round(best, 3),
        overlap_within_05m=round(float((d <= 0.5).mean()), 3),
    )
    print(json.dumps(result, ensure_ascii=False), flush=True)
    aligned = target.copy()
    aligned[:, :2] = full
    return result, aligned


def main() -> None:
    print("loading clouds ...", flush=True)
    zhong = load_cloud("9.14_caochangzhong2")
    results, aligned = {}, {"9.14_caochangzhong2": zhong}
    for name in ("9.14_caochangzuo", "9.14_caochangyou"):
        tgt = load_cloud(name)
        results[name], aligned[name] = align(tgt, zhong, name)
    (OUT / "stitch_report.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")

    fig, axes = plt.subplots(1, 2, figsize=(20, 10))
    colors = {"9.14_caochangzhong2": "#4a90d9", "9.14_caochangzuo": "#3ca35a", "9.14_caochangyou": "#e0822a"}
    ax = axes[0]
    for name, cloud in aligned.items():
        ax.scatter(cloud[:, 0], cloud[:, 1], c=colors[name], s=0.1, alpha=0.5,
                   label=name.split("caochang")[-1])
    ax.set_aspect("equal")
    ax.set_title("Stitched playground — by source")
    ax.legend(markerscale=40, fontsize=9)
    ax.grid(alpha=0.3)
    ax = axes[1]
    allpts = np.concatenate(list(aligned.values()))
    sc = ax.scatter(allpts[:, 0], allpts[:, 1], c=allpts[:, 2], s=0.1, cmap="turbo",
                    vmin=np.percentile(allpts[:, 2], 2), vmax=np.percentile(allpts[:, 2], 98))
    ax.set_aspect("equal")
    ax.set_title("Stitched playground — color = height")
    fig.colorbar(sc, ax=ax, label="z (m)", shrink=0.75)
    ax.grid(alpha=0.3)
    fig.suptitle("Diagnostic stitch of zhong2/zuo/you fans (yaw+small-translation alignment, no ground truth)", fontsize=12)
    fig.tight_layout()
    fig.savefig(OUT / "stitched_playground.png", dpi=130)
    print("saved stitched_playground.png", flush=True)


if __name__ == "__main__":
    main()
