"""KISS-ICP on production sweeps: does coverage-driven framing reach the
static-stability of 1 s accumulation windows at ~0.46 s sweep duration?

Feeds backend.sweeps output (G1) directly to KISS-ICP and reports trajectory
length / net displacement, compared against the frame-based accumulation
results in evidence/kissicp-se2-probe-20260914.md experiment 1.

Diagnostic tool, not part of the production mapping chain.
"""

import json
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "web"))

from backend import decoder  # noqa: E402
from backend.sweeps import SweepConfig, build_sweeps  # noqa: E402

OUTDIR = REPO / "2" / "kissicp_probe"


def run(name: str, pcap: Path, config: SweepConfig, tag: str) -> dict:
    from kiss_icp.config import load_config
    from kiss_icp.pipeline import KissICP

    scan = decoder.scan_pcap(pcap)
    sweeps = build_sweeps(pcap, scan.frames, config)
    cfg = load_config(None)
    # NA70 has no per-point timestamps; per project rules deskew stays off.
    # (kiss-icp 1.3.0 native deskew also crashes on all-zero timestamps.)
    cfg.data.deskew = False
    pipeline = KissICP(cfg)

    poses = []
    t0 = time.time()
    skipped = 0
    for sweep in sweeps:
        pts = np.asarray(sweep.points(install_direction=1, valid_only=True), dtype=np.float64)
        pts = pts[np.isfinite(pts).all(axis=1)]
        pts = pts[(pts[:, :3] ** 2).sum(axis=1) > 0]
        if len(pts) < 100:
            skipped += 1
            continue
        pipeline.register_frame(pts[:, :3], np.zeros(len(pts), dtype=np.float32))
        poses.append(np.asarray(pipeline.last_pose))
    traj = np.stack([p[:3, 3] for p in poses]) if poses else np.zeros((1, 3))
    steps = np.linalg.norm(np.diff(traj, axis=0), axis=1)
    result = dict(
        capture=name, sweep_config=tag, sweeps_built=len(sweeps),
        sweeps_registered=len(poses), sweeps_skipped=skipped,
        wall_time_s=round(time.time() - t0, 1),
        path_length_m=round(float(steps.sum()), 3),
        net_displacement_m=round(float(np.linalg.norm(traj[-1] - traj[0])), 3),
        step_median_m=round(float(np.median(steps)) if len(steps) else 0.0, 5),
        step_max_m=round(float(steps.max()) if len(steps) else 0.0, 3),
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
            print(f"running {name}@{tag} ...", flush=True)
            report[f"{name}@{tag}"] = run(name, pcap, config, tag)
    (OUTDIR / "sweep_kissicp_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
