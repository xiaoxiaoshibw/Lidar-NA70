"""SE(2) rotation probe: can yaw be recovered from non-ground structures?

For each accumulation window (N frames) we take the points that are clearly
above the ground plane (canopy, stand roof, seats, rails), project them to 2D,
and register consecutive windows with a yaw+translation-only ICP.  This tests
whether the 6-10 deg sensor rotations in the zuo/you captures are observable
without recapture.  Expected if yaw is observable:
  zuo ~ 6 deg, you ~ 10 deg, zhong2 ~ 0, yidong small translation.

Diagnostic tool, not part of the production mapping chain.
"""

import json
import sys
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

REPO = Path(__file__).resolve().parent.parent


def umeyama2d(src: np.ndarray, dst: np.ndarray):
    mu_s, mu_d = src.mean(axis=0), dst.mean(axis=0)
    H = (src - mu_s).T @ (dst - mu_d) / len(src)
    U, _, Vt = np.linalg.svd(H)
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    R = Vt.T @ np.diag([1.0, d]) @ U.T
    return R, mu_d - R @ mu_s


def se2_icp(src: np.ndarray, dst: np.ndarray, iters: int = 40, trim: float = 0.7):
    R, t = np.eye(2), np.zeros(2)
    tree = cKDTree(dst)
    for _ in range(iters):
        moved = src @ R.T + t
        dist, idx = tree.query(moved)
        thr = max(float(np.quantile(dist, trim)), 0.2)
        mask = dist < thr
        if mask.sum() < 50:
            break
        R, t = umeyama2d(src[mask], dst[idx[mask]])
    dist, _ = tree.query(src @ R.T + t)
    thr = max(float(np.quantile(dist, trim)), 0.2)
    return R, t, float((dist < thr).mean())


def window_clouds(prefix: Path, window: int, z_lo: float = 0.5, z_hi: float = None,
                  max_points: int = 20000):
    meta = json.loads(Path(str(prefix) + ".json").read_text(encoding="utf-8"))
    sizes = meta["frame_sizes"]
    bounds = np.concatenate([[0], np.cumsum(sizes)]).astype(np.int64)
    pts = np.memmap(Path(str(prefix) + ".bin"), dtype="<f4", mode="r").reshape(-1, 4)
    clouds = []
    for start in range(0, len(sizes), window):
        stop = min(start + window, len(sizes))
        block = np.asarray(pts[bounds[start]:bounds[stop]], dtype=np.float64)
        if len(block) < 500:
            continue
        ground_z = np.median(block[:, 2])
        h = block[:, 2] - ground_z
        keep = h > z_lo
        if z_hi is not None:
            keep &= h < z_hi
        ng = block[keep]
        if len(ng) < 100:
            continue
        xy = ng[:, :2]
        # 2D voxel decimation to keep ICP cheap and density-uniform.
        key = np.floor(xy / 0.25).astype(np.int64)
        _, idx = np.unique(key, axis=0, return_index=True)
        xy = xy[np.sort(idx)]
        if len(xy) > max_points:
            xy = xy[np.random.default_rng(0).choice(len(xy), max_points, replace=False)]
        clouds.append(xy)
    return clouds


def run(prefix: Path, window: int, z_lo: float = 0.5, z_hi: float = None) -> dict:
    clouds = window_clouds(prefix, window, z_lo=z_lo, z_hi=z_hi)
    yaw_total, path, used = 0.0, 0.0, 0
    inliers = []
    for a, b in zip(clouds, clouds[1:]):
        R, t, inl = se2_icp(a, b)
        if inl < 0.3:
            continue
        yaw_total += float(np.arctan2(R[1, 0], R[0, 0]))
        path += float(np.linalg.norm(t))
        inliers.append(inl)
        used += 1
    net = None
    return dict(
        prefix=str(prefix), window_frames=window,
        windows=len(clouds), windows_registered=used,
        median_inlier=round(float(np.median(inliers)), 3) if inliers else None,
        structure_yaw_total_deg=round(float(np.degrees(yaw_total)), 2),
        structure_path_m=round(path, 3),
    )


def main() -> None:
    outdir = REPO / "2" / "kissicp_probe"
    names = ["9.14_caochangzhong2", "9.14_caochangzuo", "9.14_caochangyou", "9.14_caochangyidong"]
    report = {}
    variants = [
        (10, 0.5, None), (30, 0.5, None),          # all non-ground
        (10, 0.5, 4.5), (30, 0.5, 4.5),            # rigid band only, canopy excluded
    ]
    for name in names:
        for window, z_lo, z_hi in variants:
            tag = f"w{window}" + (f"_z{z_lo}-{z_hi}" if z_hi else "")
            key = f"{name}@{tag}"
            print(f"running {key} ...", flush=True)
            report[key] = run(outdir / name, window, z_lo=z_lo, z_hi=z_hi)
            print(json.dumps(report[key], ensure_ascii=False), flush=True)
    (outdir / "se2_rotation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
