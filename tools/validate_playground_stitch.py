"""Validate and render a diagnostic zhong/zuo/you cross-capture stitch."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))

from backend.cross_capture import (  # noqa: E402
    CROSS_CAPTURE_VERSION, align_static_submaps, angular_coverage_deg,
    rotate_xy, validate_temporal_rigidity,
)
from backend.decoder import DECODER_VERSION, scan_pcap  # noqa: E402
from backend.mapping import VoxelMap, configuration, downsample  # noqa: E402
from backend.sweeps import SWEEP_VERSION, build_sweeps, sweep_configuration  # noqa: E402
from backend.temporal_yaw import (  # noqa: E402
    TEMPORAL_YAW_VERSION, fit_temporal_yaw, ground_heading_measurements,
    yaw_offset_at,
)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def voxel(points, size=0.25, maximum=250_000):
    if not len(points):
        return np.empty((0, 4))
    points = np.asarray(points)
    intensity = points[:, 3] if points.shape[1] > 3 else np.zeros(len(points))
    xyz, intensity = downsample(points[:, :3], intensity, size, maximum)
    return np.column_stack((xyz, intensity))


def decode_segments(path, cfg, segment_count=3, min_height=-1.5, max_height=4.5,
                    apply_temporal_yaw=False, external_total_yaw_deg=None):
    scan = scan_pcap(path)
    sweeps = build_sweeps(path, scan.frames, sweep_configuration(cfg))
    profile = fit_temporal_yaw(ground_heading_measurements(sweeps))
    if external_total_yaw_deg is not None:
        profile = dict(valid=True, reason="external_ground_block_reference",
                       measurement_count=profile.get("measurement_count", 0),
                       yaw_rate_deg_s=float(external_total_yaw_deg) / max(scan.duration, 1e-6),
                       yaw_start_deg=0.0, yaw_end_deg=float(external_total_yaw_deg),
                       residual_mad_deg=profile.get("residual_mad_deg"),
                       duration_s=scan.duration, robust_inlier_fraction=None)
    profile["applied"] = bool(apply_temporal_yaw and profile.get("valid")
                               and (external_total_yaw_deg is not None
                                    or profile.get("residual_mad_deg", 999.0) <= 2.5))
    chunks = [[] for _ in range(segment_count)]
    raw_points = kept_points = 0
    filtered_high_points = 0
    for index, sweep in enumerate(sweeps):
        points = sweep.points(valid_only=True)
        ranges = np.linalg.norm(points[:, :3], axis=1)
        points = points[np.isfinite(points).all(axis=1) & (ranges >= 2.0) & (ranges <= 60.0)]
        if len(points):
            raw_points += len(points)
            ground_z = float(np.median(points[:, 2]))
            height = points[:, 2] - ground_z
            high = height > max_height
            keep = (height >= min_height) & ~high
            filtered_high_points += int(high.sum())
            kept_points += int(keep.sum())
            selected = points[keep]
            if profile["applied"]:
                relative_time = sweep.start_time - sweeps[0].start_time
                correction = -np.degrees(yaw_offset_at(profile, relative_time))
                selected = selected.copy()
                selected[:, :2] = rotate_xy(selected[:, :2], correction)
            chunks[min(segment_count - 1, index * segment_count // len(sweeps))].append(voxel(selected, 0.25, 0))
    segments = [voxel(np.concatenate(chunk), 0.25) if chunk else np.empty((0, 4))
                for chunk in chunks]
    return dict(
        path=str(Path(path).resolve()), sha256=sha256(path), duration_seconds=scan.duration,
        packets=scan.valid_packets, sweeps=len(sweeps), segments=segments,
        full=voxel(np.concatenate(segments), 0.25),
        height_filter=dict(min_height_m=min_height, max_height_m=max_height,
                           raw_points=raw_points, kept_points=kept_points,
                           filtered_high_points=filtered_high_points,
                           filtered_fraction=(filtered_high_points / raw_points if raw_points else 0.0)),
        temporal_yaw=dict(version=TEMPORAL_YAW_VERSION, **profile),
    )


def aligned_cloud(cloud, alignment):
    result = cloud.copy()
    result[:, :2] = rotate_xy(result[:, :2], alignment["yaw_deg"]) + alignment["translation_xy_m"]
    return result


def export_map(output_dir, aligned, cfg, report):
    voxel_map = VoxelMap(cfg["map_voxel_size"], cfg["map_max_voxels"], cfg["chunk_voxels"])
    for segment_id, name in enumerate(("zhong2", "zuo", "you")):
        cloud = aligned[name]
        voxel_map.add(cloud[:, :3], cloud[:, 3], timestamp=0.0,
                      keyframe_id=segment_id, segment_id=segment_id)
    ply = output_dir / "map.ply"
    pcd = output_dir / "map.pcd"
    voxel_map.export_ply(ply)
    voxel_map.export_pcd(pcd)
    metadata = voxel_map.metadata(
        schema_version=1,
        map_kind="observed_cross_capture_coverage",
        coordinate_frame="zhong2_capture_frame",
        source_order=["zhong2", "zuo", "you"],
        angular_coverage_deg=report["angular_coverage_deg"],
        claim=report["claim"],
        loop_closure_used=False,
        imu_used=False,
        temporal_rigidity={name: value["temporal_rigidity"]
                           for name, value in report["alignments"].items()},
        height_filter=report.get("height_filter"),
    )
    (output_dir / "map_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return dict(ply=ply.name, pcd=pcd.name, metadata="map_metadata.json",
                voxel_count=voxel_map.voxel_count(), bounds=voxel_map.bounds())


def render(output, aligned, report):
    colors = {"zhong2": "#2563eb", "zuo": "#16a34a", "you": "#ea580c"}
    fig, axes = plt.subplots(1, 2, figsize=(18, 8))
    for name, cloud in aligned.items():
        sample = cloud[::max(1, len(cloud) // 100_000)]
        axes[0].scatter(sample[:, 0], sample[:, 1], s=0.15, alpha=0.45,
                        color=colors[name], label=name)
    axes[0].legend(markerscale=25)
    axes[0].set_title("Sources after one-transform alignment")
    all_points = np.concatenate(list(aligned.values()))
    sample = all_points[::max(1, len(all_points) // 220_000)]
    plot = axes[1].scatter(sample[:, 0], sample[:, 1], c=sample[:, 2], s=0.15,
                           cmap="viridis", alpha=0.55)
    axes[1].set_title("Combined diagnostic preview (height color)")
    fig.colorbar(plot, ax=axes[1], label="z (m)", shrink=0.8)
    for axis in axes:
        axis.set_aspect("equal")
        axis.grid(alpha=0.2)
        axis.set_xlabel("x (m)")
        axis.set_ylabel("y (m)")
    supported = all(item["static_submap_supported"] for item in report["alignments"].values())
    label = "STATIC SUBMAP VALIDATED" if supported else "DIAGNOSTIC ONLY — TEMPORAL RIGIDITY NOT VALIDATED"
    fig.suptitle(f"NA70 cross-capture stitch | {label} | coverage {report['angular_coverage_deg']:.1f}°")
    fig.tight_layout()
    fig.savefig(output, dpi=150)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--center", type=Path, required=True)
    parser.add_argument("--left", type=Path, required=True)
    parser.add_argument("--right", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--min-height", type=float, default=-1.5)
    parser.add_argument("--max-height", type=float, default=4.5)
    parser.add_argument("--temporal-yaw", action="store_true",
                        help="apply a low-residual ground-heading yaw profile")
    parser.add_argument("--left-yaw", type=float)
    parser.add_argument("--left-tx", type=float, default=0.0)
    parser.add_argument("--left-ty", type=float, default=0.0)
    parser.add_argument("--right-yaw", type=float)
    parser.add_argument("--right-tx", type=float, default=0.0)
    parser.add_argument("--right-ty", type=float, default=0.0)
    parser.add_argument("--left-total-yaw", type=float)
    parser.add_argument("--right-total-yaw", type=float)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    cfg = configuration()
    captures = {
        "zhong2": decode_segments(args.center, cfg, min_height=args.min_height,
                                   max_height=args.max_height, apply_temporal_yaw=args.temporal_yaw),
        "zuo": decode_segments(args.left, cfg, min_height=args.min_height,
                                max_height=args.max_height, apply_temporal_yaw=args.temporal_yaw,
                                external_total_yaw_deg=args.left_total_yaw),
        "you": decode_segments(args.right, cfg, min_height=args.min_height,
                                max_height=args.max_height, apply_temporal_yaw=args.temporal_yaw,
                                external_total_yaw_deg=args.right_total_yaw),
    }
    reference = captures["zhong2"]["full"]
    report = dict(
        schema_version=1,
        versions=dict(decoder=DECODER_VERSION, sweep=SWEEP_VERSION,
                      cross_capture=CROSS_CAPTURE_VERSION),
        environment=dict(platform=platform.platform(), python=sys.version.split()[0]),
        inputs={name: {key: value for key, value in capture.items()
                       if key not in ("segments", "full")}
                for name, capture in captures.items()},
        alignments={},
    )
    aligned = {"zhong2": reference}
    for name in ("zuo", "you"):
        fixed_yaw = args.left_yaw if name == "zuo" else args.right_yaw
        if fixed_yaw is not None:
            full = dict(yaw_deg=fixed_yaw,
                        translation_xy_m=[args.left_tx, args.left_ty] if name == "zuo"
                        else [args.right_tx, args.right_ty],
                        selection="fixed_transform_hypothesis")
            temporal = []
            rigidity = dict(rigid=False, reason="external_fixed_transform_hypothesis")
            supported = False
        else:
            full = align_static_submaps(captures[name]["full"], reference)
            temporal = [align_static_submaps(segment, reference) for segment in captures[name]["segments"]]
            rigidity = validate_temporal_rigidity(temporal)
            heldout = full["heldout"]
            symmetric = full["full_symmetric"]
            supported = bool(
                rigidity["rigid"]
                and heldout["overlap"]["0.5"] >= 0.60
                and symmetric["symmetric_overlap"]["0.5"] >= 0.50
                and heldout["p95_m"] <= 0.50
                and full["peak_margin_m"] is not None and full["peak_margin_m"] >= 0.02
            )
        report["alignments"][name] = dict(
            full=full, temporal_segments=temporal, temporal_rigidity=rigidity,
            static_submap_supported=supported,
            status="PASS" if supported else "FAIL",
        )
        aligned[name] = aligned_cloud(captures[name]["full"], full)
    report["angular_coverage_deg"] = angular_coverage_deg(list(aligned.values()))
    report["height_filter"] = dict(min_height_m=args.min_height, max_height_m=args.max_height,
                                    per_capture={name: capture["height_filter"]
                                                 for name, capture in captures.items()})
    report["claim"] = (
        "diagnostic_fixed_transform_hypothesis" if args.left_yaw is not None and args.right_yaw is not None else
        "validated_static_submap_stitch" if all(
            item["static_submap_supported"] for item in report["alignments"].values())
        else "observed_coverage_map_with_unresolved_temporal_distortion"
    )
    report["map"] = export_map(args.output_dir, aligned, cfg, report)
    json_path = args.output_dir / "stitch-validation.json"
    image_path = args.output_dir / "stitched-playground-validated.png"
    render(image_path, aligned, report)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2,
                                    allow_nan=False), encoding="utf-8")
    print(json.dumps({
        "report": str(json_path), "image": str(image_path),
        "claim": report["claim"], "coverage_deg": report["angular_coverage_deg"],
        "map": report["map"],
        "alignments": {name: dict(status=value["status"],
                                    yaw=value["full"]["yaw_deg"],
                                    yaw_spread=value["temporal_rigidity"].get("yaw_spread_deg"))
                       for name, value in report["alignments"].items()},
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
