"""Near/mid-field rigid structure analysis for the 2026-09-14 playground dumps.

Question: do any persistent rigid structures exist close enough to constrain
yaw (the unobservable degree of freedom)?  For every capture we accumulate the
whole recording, cluster the occupied voxels, and report per cluster:
geometry (PCA linearity/planarity), height, per-frame detection rate, and
azimuth drift between the first and last third of the recording (world-fixed
structures sweep in sensor azimuth while the radar rotates; anything mounted
on the radar stand stays fixed).

This is a diagnostic tool, not part of the production mapping chain.
"""

import json
import sys
from pathlib import Path

import numpy as np
from scipy import ndimage

REPO = Path(__file__).resolve().parent.parent


def analyze(prefix: Path) -> dict:
    meta = json.loads(Path(str(prefix) + ".json").read_text(encoding="utf-8"))
    sizes = meta["frame_sizes"]
    bounds = np.concatenate([[0], np.cumsum(sizes)]).astype(np.int64)
    pts = np.memmap(Path(str(prefix) + ".bin"), dtype="<f4", mode="r").reshape(-1, 4)
    xyz = np.asarray(pts[:, :3])
    r = np.linalg.norm(xyz, axis=1)

    result = dict(prefix=str(prefix), frames=len(sizes), duration_s=meta["duration_s"])
    for name, lo, hi in (("near", 0.5, 10.0), ("mid", 10.0, 30.0)):
        band = (r >= lo) & (r < hi)
        counts = np.bincount(np.searchsorted(bounds[1:], np.nonzero(band)[0], side="left"),
                             minlength=len(sizes))
        per_frame = np.array([band[bounds[i]:bounds[i + 1]].sum() for i in range(len(sizes))])
        entry = dict(
            total_points=int(band.sum()),
            frames_with_points=int((per_frame > 0).sum()),
            frames_total=len(sizes),
            points_per_frame_median=int(np.median(per_frame[per_frame > 0])) if (per_frame > 0).any() else 0,
        )
        if band.sum() < 500:
            result[name] = entry
            continue

        sel = xyz[band]
        # Voxelize at 0.25 m and keep dense voxels only.
        voxel = np.floor(sel / 0.25).astype(np.int64)
        keys, inv, cnt = np.unique(voxel, axis=0, return_inverse=True, return_counts=True)
        dense = keys[cnt >= 8]
        if len(dense) == 0:
            result[name] = entry
            continue
        offset = dense.min(axis=0)
        grid = np.zeros(tuple(dense.max(axis=0) - offset + 1), dtype=bool)
        grid[tuple((dense - offset).T)] = True
        labels, n_clusters = ndimage.label(grid, structure=np.ones((3, 3, 3), dtype=bool))
        if n_clusters == 0:
            result[name] = entry
            continue
        cluster_ids, cluster_sizes = np.unique(labels[labels > 0], return_counts=True)
        order = np.argsort(cluster_sizes)[::-1][:3]

        clusters = []
        for rank, ci in enumerate(order):
            mask = labels == cluster_ids[ci]
            cell_xyz = (np.argwhere(mask) + 0.5 + offset) * 0.25
            ctr = cell_xyz.mean(axis=0)
            centered = cell_xyz - ctr
            cov = np.cov(centered.T) if len(cell_xyz) > 3 else np.zeros((3, 3))
            eig = np.sort(np.linalg.eigvalsh(cov))[::-1]
            eig = np.maximum(eig, 1e-9)
            linearity = float((eig[0] - eig[1]) / eig[0])
            planarity = float((eig[1] - eig[2]) / eig[0])
            # Per-frame detection: fraction of frames with >=3 points inside
            # the cluster's axis-aligned bounding box (padded 0.3 m).
            lo_b = cell_xyz.min(axis=0) - 0.3
            hi_b = cell_xyz.max(axis=0) + 0.3
            box = ((xyz >= lo_b) & (xyz < hi_b)).all(axis=1) & (r >= lo) & (r < hi)
            frame_ids = np.searchsorted(bounds[1:], np.nonzero(box)[0], side="left")
            detected = len(np.unique(frame_ids))
            # Azimuth in first vs last third of the recording.
            t_first = frame_ids < len(sizes) / 3
            t_last = frame_ids >= 2 * len(sizes) / 3
            az = np.degrees(np.arctan2(xyz[box][:, 1], xyz[box][:, 0]))
            az_first = np.median(az[t_first]) if t_first.any() else None
            az_last = np.median(az[t_last]) if t_last.any() else None
            clusters.append(dict(
                rank=rank + 1,
                voxel_cells=int(cluster_sizes[ci]),
                approx_points=int(cluster_sizes[ci] * 8),
                centroid_xyz=[round(float(v), 2) for v in ctr],
                range_m=round(float(np.linalg.norm(ctr)), 2),
                height_z=[round(float(cell_xyz[:, 2].min()), 2), round(float(cell_xyz[:, 2].max()), 2)],
                extent_m=[round(float(v), 2) for v in (cell_xyz.max(axis=0) - cell_xyz.min(axis=0))],
                linearity=round(linearity, 3),
                planarity=round(planarity, 3),
                detection_rate=round(detected / len(sizes), 3),
                azimuth_first_third=round(float(az_first), 1) if az_first is not None else None,
                azimuth_last_third=round(float(az_last), 1) if az_last is not None else None,
            ))
        entry["clusters"] = clusters
        result[name] = entry
    return result


def main() -> None:
    outdir = REPO / "2" / "kissicp_probe"
    names = sys.argv[1:] or ["9.14_caochangzhong2", "9.14_caochangzuo",
                             "9.14_caochangyou", "9.14_caochangyidong"]
    report = {}
    for name in names:
        print(f"analyzing {name} ...", flush=True)
        report[name] = analyze(outdir / name)
    (outdir / "nearfield_structure_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
