"""Offline command-line runner for the ROS-independent motion detector.

This is intentionally independent of rclpy.  It lets a developer verify the
voxel/background algorithm on a normal Python installation before installing
ROS 2 in WSL2.  Input files may be ``.npy`` arrays with shape ``[F, N, 3+]``
or ``.npz`` files containing a ``frames`` array with the same shape.  Each
frame must contain XYZ in its first three columns; a fourth intensity column
is accepted and ignored by the detector.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

import numpy as np

from .motion_core import MotionDetectorConfig, VoxelMotionDetector


def _synthetic_frames() -> Iterable[np.ndarray]:
    """Yield an empty-scene warmup followed by a persistent moving object."""
    x, y = np.meshgrid(np.linspace(-3.0, 3.0, 25), np.linspace(-3.0, 3.0, 25))
    background_xyz = np.column_stack([x.ravel(), y.ravel(), np.zeros(x.size)])
    background = np.column_stack(
        [background_xyz, np.full(len(background_xyz), 20.0)]
    ).astype(np.float32)

    rng = np.random.default_rng(7)
    object_xyz = rng.normal(
        loc=(5.0, 0.0, 1.0), scale=(0.12, 0.12, 0.35), size=(60, 3)
    )
    moving = np.column_stack([object_xyz, np.full(len(object_xyz), 150.0)]).astype(
        np.float32
    )

    for _ in range(5):
        yield background
    for _ in range(3):
        yield np.vstack([background, moving])


def _load_frames(path: Path) -> Iterable[np.ndarray]:
    loaded = np.load(path, allow_pickle=False)
    if isinstance(loaded, np.lib.npyio.NpzFile):
        if "frames" not in loaded:
            raise ValueError(f"{path} must contain an array named 'frames'")
        frames = loaded["frames"]
    else:
        frames = loaded

    if frames.ndim != 3 or frames.shape[2] < 3:
        raise ValueError(
            f"{path} must have shape [frames, points, channels>=3], got {frames.shape}"
        )
    return (np.asarray(frame, dtype=np.float32) for frame in frames)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        help="optional .npy/.npz frame array; omit to run the synthetic smoke test",
    )
    parser.add_argument("--warmup-frames", type=int, default=5)
    parser.add_argument("--min-moving-points", type=int, default=30)
    parser.add_argument("--min-moving-ratio", type=float, default=0.001)
    parser.add_argument("--confirmation-frames", type=int, default=2)
    parser.add_argument("--clear-frames", type=int, default=3)
    return parser


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    config = MotionDetectorConfig(
        warmup_frames=args.warmup_frames,
        min_moving_points=args.min_moving_points,
        min_moving_ratio=args.min_moving_ratio,
        confirmation_frames=args.confirmation_frames,
        clear_frames=args.clear_frames,
    )
    detector = VoxelMotionDetector(config)
    frames = _synthetic_frames() if args.input is None else _load_frames(args.input)

    detected = False
    for index, frame in enumerate(frames, start=1):
        result = detector.process(frame)
        detected = detected or result.has_motion
        print(
            f"frame={index:04d} ready={int(result.ready)} "
            f"has_motion={int(result.has_motion)} "
            f"moving_count={result.moving_count} "
            f"moving_ratio={result.moving_ratio:.6f}"
        )
    if args.input is None and not detected:
        raise RuntimeError("synthetic smoke test did not detect the moving object")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
