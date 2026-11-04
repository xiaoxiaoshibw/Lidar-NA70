"""SYNTHETIC end-to-end scene build: known scene + known trajectory -> map.ply.

The purpose is to prove the assembly path (pose interpolation, point-time
transform, voxel map, source provenance, export) against a known answer.  It is
not a real capture, not SLAM, and not an accuracy acceptance for NA70.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "na70_core"))
from na70_core import scene  # noqa: E402
from na70_core.calibration import rotation as rotation_lib  # noqa: E402

SCENE = {
    "ground_z": -1.0,
    "back_x": 8.0,
    "side_y": 4.0,
    "span_x": (-5.0, 15.0),
    "span_y": (-6.0, 6.0),
}


def pose_arrays(times):
    times = np.asarray(times, dtype=np.float64)
    positions = np.column_stack([0.60 * times - 2.0, 0.02 * times, np.zeros(len(times))])
    # A small wobble makes 10 Hz pose interpolation an approximation, not an
    # exact reconstruction of the generating motion.
    yaw = np.radians(20.0 * times / 4.0 + 1.5 * np.sin(2.0 * np.pi * 0.8 * times))
    rotations = np.zeros((len(times), 3, 3))
    rotations[:, 0, 0] = np.cos(yaw)
    rotations[:, 0, 1] = -np.sin(yaw)
    rotations[:, 1, 0] = np.sin(yaw)
    rotations[:, 1, 1] = np.cos(yaw)
    rotations[:, 2, 2] = 1.0
    return positions, rotations


def pose(time):
    positions, rotations = pose_arrays([time])
    return positions[0], rotations[0]


def scene_points(count=12000, seed=17):
    generator = np.random.default_rng(seed)
    ground = np.column_stack([generator.uniform(*SCENE["span_x"], count),
                              generator.uniform(*SCENE["span_y"], count),
                              np.full(count, SCENE["ground_z"])])
    back = np.column_stack([np.full(count, SCENE["back_x"]),
                            generator.uniform(*SCENE["span_y"], count),
                            generator.uniform(SCENE["ground_z"], 3.0, count)])
    side = np.column_stack([generator.uniform(*SCENE["span_x"], count),
                            np.full(count, SCENE["side_y"]),
                            generator.uniform(SCENE["ground_z"], 3.0, count)])
    return np.concatenate([ground, back, side], axis=0)


def scene_distance(points):
    return np.min(np.column_stack([np.abs(points[:, 2] - SCENE["ground_z"]),
                                   np.abs(points[:, 0] - SCENE["back_x"]),
                                   np.abs(points[:, 1] - SCENE["side_y"])]), axis=1)


def visible(points_sensor):
    x, y, z = points_sensor[:, 0], points_sensor[:, 1], points_sensor[:, 2]
    forward = x > 0.5
    azimuth = np.abs(np.arctan2(y, x)) < math.radians(65.0)
    elevation = np.abs(np.arctan2(z, np.sqrt(x * x + y * y))) < math.radians(30.0)
    return forward & azimuth & elevation


def save_preview(path, result, positions):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None
    figure = plt.figure(figsize=(10, 7), dpi=120)
    axes = figure.add_subplot(projection="3d")
    points = result.points.astype(np.float64)
    if len(points) > 30000:
        points = points[np.linspace(0, len(points) - 1, 30000).astype(np.int64)]
    axes.scatter(points[:, 0], points[:, 1], points[:, 2], c=points[:, 2], s=0.3,
                 cmap="viridis", linewidths=0, depthshade=False)
    axes.plot(positions[:, 0], positions[:, 1], positions[:, 2], color="#d62728", lw=2.0,
              label="trajectory (synthetic)")
    axes.set_xlabel("x [m]")
    axes.set_ylabel("y [m]")
    axes.set_zlabel("z [m]")
    axes.set_title("NA70 scene assembly - SYNTHETIC")
    axes.view_init(elev=22, azim=-65)
    axes.set_box_aspect((2.0, 1.2, 0.5))
    axes.legend(loc="upper right")
    figure.tight_layout()
    figure.savefig(path)
    plt.close(figure)
    return path.name


def run(output, *, frames=50, voxel_size=0.05):
    output.mkdir(parents=True, exist_ok=False)
    truth = scene_points()
    duration = 8.0
    node_times = np.arange(0.0, duration + 0.1, 0.1)
    node_positions, node_rotations = pose_arrays(node_times)
    node_quaternions = np.array([rotation_lib.rotation_matrix_to_quaternion(r) for r in node_rotations])
    trajectory = scene.Trajectory(times=node_times, positions=node_positions,
                                  quaternions=node_quaternions, frame="lidar")
    builder = scene.MapBuilder(trajectory, voxel_size=voxel_size, max_gap_s=0.2)
    generator = np.random.default_rng(23)
    for frame_index, t0 in enumerate(np.linspace(0.1, duration - 0.1, frames)):
        times = t0 + generator.uniform(-0.04, 0.04, len(truth))
        positions, rotations = pose_arrays(times)
        points = np.einsum("nij,nj->ni", np.transpose(rotations, (0, 2, 1)), truth - positions)
        points += generator.normal(0.0, 0.005, points.shape)
        mask = visible(points)
        builder.add_frame(points[mask], times[mask], frame_index=frame_index)
    result = builder.finish()
    result.save_ply(output / "map.ply")
    preview = save_preview(output / "preview.png", result, node_positions)
    distance = scene_distance(result.points.astype(np.float64))
    summary = {
        "schema": "na70-scene-synthetic-v1",
        "evidence_kind": "SYNTHETIC",
        "scene": SCENE,
        "trajectory": {"duration_s": duration, "nodes": int(len(node_times)),
                       "frames": frames},
        "map": result.stats,
        "preview": preview,
        "accuracy_synthetic": {
            "distance_p50_m": float(np.percentile(distance, 50)),
            "distance_p95_m": float(np.percentile(distance, 95)),
            "distance_max_m": float(distance.max()),
            "points": int(len(distance)),
        },
        "status": "SYNTHETIC_PASS" if np.percentile(distance, 95) < 0.05 else "SYNTHETIC_FAIL",
        "claims": {"slam_validated": False, "hardware_validated": False,
                   "real_scene_reconstructed": False},
        "limitations": ["合成场景与位姿；不证明 NA70 SLAM、同步或真实操场重建",
                        "点云由全部场景点变换得到，未模拟回波强度/遮挡/测距噪声"],
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (output / "REPORT.md").write_text(
        "# 场景重建 SYNTHETIC 验证\n\n"
        f"- 地图点：{result.stats['points']}，体素：{result.stats['voxels']}，"
        f"帧：{result.stats['frames']}\n"
        f"- 到已知平面距离：P50 {summary['accuracy_synthetic']['distance_p50_m']:.4f} m，"
        f"P95 {summary['accuracy_synthetic']['distance_p95_m']:.4f} m\n"
        f"- 结论：**{summary['status']}**\n\n"
        "这是装配数学验证，不代表真实操场扫描或 SLAM 精度。\n", encoding="utf-8")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--frames", type=int, default=50)
    parser.add_argument("--voxel-size", type=float, default=0.05)
    args = parser.parse_args(argv)
    summary = run(args.output, frames=args.frames, voxel_size=args.voxel_size)
    print(json.dumps({"status": summary["status"], "points": summary["map"]["points"],
                      "p95_m": summary["accuracy_synthetic"]["distance_p95_m"]}, ensure_ascii=False))
    return 0 if summary["status"] == "SYNTHETIC_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())