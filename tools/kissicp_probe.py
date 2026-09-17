"""KISS-ICP independent probe for the 2026-09-14 playground captures.

Purpose: run an established third-party odometry (KISS-ICP, PRBonn) on the four
playground PCAPs to get an independent verdict on whether the data can support
odometry.  This is a diagnostic tool, not part of the production mapping chain;
it must not be presented as R7 acceptance evidence.

Subcommands:
  dump  decode a PCAP with the production decoder and write frame points to
        a raw float32 .bin plus a metadata JSON (zero-distance and non-finite
        points removed, same rule as production rendering).
  run   run KISS-ICP over a dumped capture and write trajectory + summary JSON.

Usage:
  python tools/kissicp_probe.py dump  <pcap> <outdir>
  python tools/kissicp_probe.py run   <dumpdir/prefix> <outdir>
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "web"))

from backend import decoder  # noqa: E402


def cmd_dump(pcap: Path, outdir: Path) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    prefix = pcap.stem
    scan = decoder.scan_pcap(pcap)
    frames = scan.frames
    bin_path = outdir / f"{prefix}.bin"
    kept = 0
    sizes = []
    times = []
    t0 = time.time()
    with pcap.open("rb") as src, bin_path.open("wb") as dst:
        for f in frames:
            pts = decoder.decode_frame(pcap, f, source=src)
            pts = pts[np.isfinite(pts[:, :3]).all(axis=1) & (pts[:, 0] ** 2 + pts[:, 1] ** 2 + pts[:, 2] ** 2 > 0)]
            pts.astype("<f4").tofile(dst)
            sizes.append(int(len(pts)))
            times.append(float(f["time"]))
            kept += int(len(pts))
            if (len(sizes) % 200) == 0:
                print(f"  {len(sizes)}/{len(frames)} frames decoded ({time.time() - t0:.0f}s)", flush=True)
    meta = dict(
        pcap=str(pcap),
        frames=len(frames),
        points_kept=kept,
        points_raw=scan.valid_packets * 200,
        valid_packets=scan.valid_packets,
        invalid_packets=scan.invalid_packets,
        ignored_packets=scan.ignored_packets,
        duration_s=scan.duration,
        warnings=scan.warnings,
        frame_sizes=sizes,
        frame_times=times,
        format="float32 x,y,z,intensity, little-endian, raw stream",
    )
    (outdir / f"{prefix}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"dumped {prefix}: {len(frames)} frames, {kept} points -> {bin_path}")


def cmd_run(prefix: Path, outdir: Path, accumulate: int = 1) -> None:
    from kiss_icp.config import load_config
    from kiss_icp.pipeline import KissICP

    meta = json.loads(Path(str(prefix) + ".json").read_text(encoding="utf-8"))
    sizes = meta["frame_sizes"]
    times = meta["frame_times"]
    bin_path = Path(str(prefix) + ".bin")
    points_mm = np.memmap(bin_path, dtype="<f4", mode="r").reshape(-1, 4)
    bounds = np.concatenate([[0], np.cumsum(sizes)]).astype(np.int64)

    config = load_config(None)
    # NA70 raw data has no per-point timestamps (packet time only); per project
    # rules we do not fabricate them, so KISS-ICP deskew must be disabled.
    # (kiss-icp 1.3.0 native deskew also crashes on all-zero timestamps.)
    config.data.deskew = False
    pipeline = KissICP(config)
    poses = []
    t0 = time.time()
    skipped = 0
    windows = [(i, min(i + accumulate, len(sizes))) for i in range(0, len(sizes), accumulate)]
    window_step_m = []
    for wi, (start, stop) in enumerate(windows):
        block = points_mm[bounds[start]:bounds[stop], :3]
        if len(block) == 0:
            skipped += 1
            poses.append(poses[-1] if poses else np.eye(4))
            continue
        frame = np.asarray(block, dtype=np.float64)
        pipeline.register_frame(frame, np.zeros(len(frame), dtype=np.float32))
        poses.append(np.asarray(pipeline.last_pose))
        if (wi + 1) % 20 == 0:
            print(f"  {wi + 1}/{len(windows)} windows ({time.time() - t0:.0f}s)", flush=True)
    n_frames = len(windows)
    elapsed = time.time() - t0

    traj = np.stack([p[:3, 3] for p in poses])
    yaw = np.arctan2([p[1, 0] for p in poses], [p[0, 0] for p in poses])
    steps = np.linalg.norm(np.diff(traj, axis=0), axis=1)
    valid = steps[np.isfinite(steps)]
    path_len = float(steps.sum())
    summary = dict(
        prefix=str(prefix),
        accumulate_frames=accumulate,
        windows_total=n_frames,
        frames_empty_skipped=skipped,
        wall_time_s=round(elapsed, 1),
        path_length_m=round(path_len, 3),
        net_displacement_m=round(float(np.linalg.norm(traj[-1] - traj[0])), 3),
        step_median_m=round(float(np.median(valid)) if len(valid) else 0.0, 5),
        step_p95_m=round(float(np.percentile(valid, 95)) if len(valid) else 0.0, 5),
        step_max_m=round(float(valid.max()) if len(valid) else 0.0, 3),
        yaw_total_deg=round(float(np.degrees(yaw[-1] - yaw[0])), 2),
        frame_times_s=times,
    )
    suffix = f"_acc{accumulate}" if accumulate > 1 else ""
    np.savez(outdir / f"{prefix.name}{suffix}_result.npz", trajectory=traj, yaw=yaw, steps=steps)
    (outdir / f"{prefix.name}{suffix}_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "frame_times_s"},
                     ensure_ascii=False, indent=1))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump")
    d.add_argument("pcap", type=Path)
    d.add_argument("outdir", type=Path)
    r = sub.add_parser("run")
    r.add_argument("prefix", type=Path, help="path to <prefix>.json dump metadata (without suffix ok)")
    r.add_argument("outdir", type=Path)
    r.add_argument("--accumulate", type=int, default=1,
                   help="concatenate N consecutive frames into one registration window")
    args = parser.parse_args()
    if args.cmd == "dump":
        cmd_dump(args.pcap, args.outdir)
    else:
        prefix = args.prefix.with_suffix("") if args.prefix.suffix == ".json" else args.prefix
        cmd_run(prefix, args.outdir, accumulate=args.accumulate)


if __name__ == "__main__":
    main()
