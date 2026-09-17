"""Bridge experiment: production G1 sweeps as input to the SE(2) rigid-band probe.

The 2026-09-14 probe (tools/se2_rotation_probe.py) showed the you 10.1 deg
rotation is recoverable from 10-frame accumulation windows + rigid-band
structures, but window size sensitivity (3 s windows collapse) makes the
estimator unreliable.  This script replaces naive N-frame concatenation with
the production coverage-driven sweep builder (backend.sweeps, G1 PASS) and
re-runs the same SE(2) probe, reporting per-window yaw increments so stability
can be judged (smooth accumulation vs jumps).

Diagnostic tool, not part of the production mapping chain.
"""

import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "web"))
sys.path.insert(0, str(REPO / "tools"))

from backend import decoder  # noqa: E402
from backend.sweeps import SweepConfig, build_sweeps  # noqa: E402
from se2_rotation_probe import se2_icp  # noqa: E402

OUTDIR = REPO / "2" / "kissicp_probe"


def sweep_clouds(sweeps: list, z_lo: float, z_hi: float, max_points: int = 20000) -> list:
    clouds = []
    for sweep in sweeps:
        pts = np.asarray(sweep.points(install_direction=1, valid_only=True), dtype=np.float64)
        pts = pts[np.isfinite(pts).all(axis=1)]
        pts = pts[(pts[:, :3] ** 2).sum(axis=1) > 0]
        if len(pts) < 500:
            continue
        ground_z = np.median(pts[:, 2])
        h = pts[:, 2] - ground_z
        band = pts[(h > z_lo) & (h < z_hi)][:, :2]
        if len(band) < 100:
            continue
        key = np.floor(band / 0.25).astype(np.int64)
        _, idx = np.unique(key, axis=0, return_index=True)
        band = band[np.sort(idx)]
        if len(band) > max_points:
            band = band[np.random.default_rng(0).choice(len(band), max_points, replace=False)]
        clouds.append(band)
    return clouds


def run(name: str, pcap: Path, config: SweepConfig, tag: str) -> dict:
    scan = decoder.scan_pcap(pcap)
    sweeps = build_sweeps(pcap, scan.frames, config)
    result = dict(capture=name, sweep_config=tag, sweeps_built=len(sweeps))
    for z_hi_label, z_lo, z_hi in (("all", 0.5, None), ("rigid", 0.5, 4.5)):
        clouds = sweep_clouds(sweeps, z_lo, z_hi if z_hi else 1e9)
        yaw_total, path, used = 0.0, 0.0, 0
        increments, inliers = [], []
        for a, b in zip(clouds, clouds[1:]):
            R, t, inl = se2_icp(a, b)
            if inl < 0.3:
                continue
            dyaw = float(np.degrees(np.arctan2(R[1, 0], R[0, 0])))
            yaw_total += dyaw
            path += float(np.linalg.norm(t))
            increments.append(dyaw)
            inliers.append(inl)
            used += 1
        inc = np.abs(increments) if increments else np.array([0.0])
        result[z_hi_label] = dict(
            sweeps_with_band=len(clouds), windows_registered=used,
            median_inlier=round(float(np.median(inliers)), 3) if inliers else None,
            yaw_total_deg=round(float(yaw_total), 2),
            structure_path_m=round(path, 2),
            yaw_inc_p95_deg=round(float(np.percentile(inc, 95)), 4),
            yaw_inc_max_deg=round(float(inc.max()), 4),
            windows_over_1deg=int((inc > 1.0).sum()),
        )
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


def main() -> None:
    captures = {
        "zhong2": REPO / "2" / "9.14_caochangzhong2.pcap",
        "zuo": REPO / "2" / "9.14_caochangzuo.pcap",
        "you": REPO / "2" / "9.14_caochangyou.pcap",
        "yidong": REPO / "2" / "9.14_caochangyidong.pcap",
    }
    configs = {
        "default_0.75s": SweepConfig(),
        "long_2.5s": SweepConfig(min_packets=500, max_packets=2500, max_duration=2.5),
    }
    report = {}
    for name, pcap in captures.items():
        for tag, config in configs.items():
            key = f"{name}@{tag}"
            print(f"running {key} ...", flush=True)
            report[key] = run(name, pcap, config, tag)
    (OUTDIR / "sweep_se2_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
