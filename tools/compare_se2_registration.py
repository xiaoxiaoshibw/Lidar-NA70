"""Compare constrained SE(2) registration candidates on NA70 sweeps."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import sys
import time

import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))

from backend.decoder import DECODER_VERSION, scan_pcap  # noqa: E402
from backend.mapping import configuration, estimate_normals, preprocess  # noqa: E402
from backend.registration_se2 import (  # noqa: E402
    SE2_REGISTRATION_VERSION, point_to_plane_observability, register_se2,
    scene_structure_statistics,
)
from backend.sweeps import build_sweeps, sweep_configuration  # noqa: E402


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def finite_json(value):
    if isinstance(value, dict):
        return {key: finite_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [finite_json(item) for item in value]
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def pose_metrics(pose):
    if pose is None:
        return None
    pose = np.asarray(pose)
    return dict(
        x=float(pose[0, 3]), y=float(pose[1, 3]),
        translation_m=float(np.linalg.norm(pose[:2, 3])),
        yaw_deg=float(Rotation.from_matrix(pose[:3, :3]).as_euler("zyx", degrees=True)[0]),
    )


def selected_pairs(count, maximum, stride=1):
    if count <= stride:
        return []
    return sorted(set(np.linspace(stride, count - 1, min(maximum, count - stride), dtype=int).tolist()))


def analyze(path, cfg, maximum_pairs, stride):
    started = time.perf_counter()
    scan = scan_pcap(path)
    sweeps = build_sweeps(path, scan.frames, sweep_configuration(cfg))
    rows = []
    for current_index in selected_pairs(len(sweeps), maximum_pairs, stride):
        previous_index = current_index - stride
        target, _ = preprocess(sweeps[previous_index].points(), cfg)
        source, _ = preprocess(sweeps[current_index].points(), cfg)
        normals = estimate_normals(target, cfg["normal_neighbors"])
        row = dict(
            previous_sweep=previous_index,
            current_sweep=current_index,
            time=sweeps[current_index].start_time - sweeps[0].start_time,
            target_points=len(target), source_points=len(source),
            structures=scene_structure_statistics(target, cfg["normal_neighbors"]),
            se3_observability=point_to_plane_observability(
                target, normals, cfg["se2_observability_ratio"]),
            methods={},
        )
        for method in ("point_to_plane", "gicp", "ndt"):
            estimate, quality = register_se2(source, target, np.eye(4), cfg, method)
            candidate = quality.get("candidate_pose")
            row["methods"][method] = dict(
                quality=quality,
                accepted_pose=pose_metrics(estimate),
                candidate_pose=pose_metrics(candidate),
            )
        rows.append(finite_json(row))

    summary = {}
    for method in ("point_to_plane", "gicp", "ndt"):
        entries = [row["methods"][method] for row in rows]
        accepted = [entry["accepted_pose"] for entry in entries if entry["accepted_pose"] is not None]
        candidates = [entry["candidate_pose"] for entry in entries if entry["candidate_pose"] is not None]
        states = {}
        for entry in entries:
            state = entry["quality"]["state"]
            states[state] = states.get(state, 0) + 1
        summary[method] = dict(
            states=states,
            accepted_count=len(accepted),
            accepted_yaw_median_deg=(float(np.median([item["yaw_deg"] for item in accepted]))
                                     if accepted else None),
            accepted_translation_median=(float(np.median([item["translation_m"] for item in accepted]))
                                         if accepted else None),
            candidate_yaw_median_deg=(float(np.median([item["yaw_deg"] for item in candidates]))
                                      if candidates else None),
            candidate_translation_median=(float(np.median([item["translation_m"] for item in candidates]))
                                          if candidates else None),
        )
    return finite_json(dict(
        schema_version=1,
        versions=dict(decoder=DECODER_VERSION, registration=SE2_REGISTRATION_VERSION),
        input=dict(path=str(path.resolve()), sha256=file_hash(path), packets=scan.valid_packets,
                   sweep_count=len(sweeps), analyzed_pairs=len(rows), sweep_stride=stride),
        summary=summary, pairs=rows,
        elapsed_seconds=round(time.perf_counter() - started, 3),
    ))


def report_markdown(report):
    lines = [f"# {Path(report['input']['path']).name} SE(2) candidate comparison", "",
             f"- SHA-256: `{report['input']['sha256']}`",
             f"- Sweeps / sampled adjacent pairs: {report['input']['sweep_count']} / {report['input']['analyzed_pairs']}",
             "", "| Method | States | Accepted | Accepted yaw p50 | Candidate yaw p50 | Candidate translation p50 |",
             "|---|---|---:|---:|---:|---:|"]
    for method, item in report["summary"].items():
        lines.append(f"| {method} | `{json.dumps(item['states'], ensure_ascii=False)}` | "
                     f"{item['accepted_count']} | {item['accepted_yaw_median_deg']} | "
                     f"{item['candidate_yaw_median_deg']} | {item['candidate_translation_median']} |")
    lines.extend(["", "Rejected candidate poses are diagnostic only and are never fused.", ""])
    return "\n".join(lines)


def write_index(output_dir, command, cfg):
    manifest = dict(command=command, platform=platform.platform(),
                    python=sys.version.split()[0], config=cfg, reports=[])
    lines = ["# NA70 playground SE(2) candidate summary", "",
             "Rejected candidates are diagnostics only and are never fused.", "",
             "| Capture | Pairs | Method | Accepted | States | Candidate yaw p50 | Candidate translation p50 |",
             "|---|---:|---|---:|---|---:|---:|"]
    for json_path in sorted(output_dir.glob("*-se2.json")):
        report = json.loads(json_path.read_text(encoding="utf-8"))
        manifest["reports"].append(dict(input=report["input"]["path"], json=json_path.name,
                                        markdown=json_path.with_suffix(".md").name))
        for method, item in report["summary"].items():
            lines.append(f"| {Path(report['input']['path']).name} | {report['input']['analyzed_pairs']} | "
                         f"{method} | {item['accepted_count']} | `{json.dumps(item['states'], ensure_ascii=False)}` | "
                         f"{item['candidate_yaw_median_deg']} | {item['candidate_translation_median']} |")
    (output_dir / "se2-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pcaps", nargs="*", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--pairs", type=int, default=12)
    parser.add_argument("--stride", type=int, default=1)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    cfg = configuration()
    for path in args.pcaps:
        if args.stride < 1:
            raise ValueError("stride 必须至少为 1")
        report = analyze(path, cfg, args.pairs, args.stride)
        stem = path.stem + "-se2"
        json_path = args.output_dir / f"{stem}.json"
        md_path = args.output_dir / f"{stem}.md"
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2,
                                        allow_nan=False), encoding="utf-8")
        md_path.write_text(report_markdown(report), encoding="utf-8")
        print(f"{path.name}: {report['elapsed_seconds']} s")
    write_index(args.output_dir, " ".join(sys.argv), cfg)


if __name__ == "__main__":
    main()
