"""Run the wire pipeline over the SYNTHETIC scenario matrix and record evidence.

Every scenario is SYNTHETIC and tagged as such in all artifacts. Results test
code behaviour only; they never enter real TP/range denominators and must not
be quoted as NA70 wire-detection capability.

Usage:
  ./web/.venv/Scripts/python.exe tools/run_wire_synthetic_matrix.py \
      --output Plan/evidence/hv-line/<run-id> [--scenario name ...] [--seed N]
      [--strict]
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))
sys.path.insert(0, str(ROOT))

from na70_core import RayBatch  # noqa: E402
from na70_core.wire import (SCENARIO_BUILDERS, WindowInputs, build_scenario,  # noqa: E402
                            load_wire_config, run_detection_window)
from na70_core.windows import PacketWindowRing, RingLimits  # noqa: E402


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _selected_window(batch: RayBatch, duration_s: float):
    frames = int(batch.frame_indices.max()) + 1
    per_frame = batch.ray_count // frames
    ring = PacketWindowRing(RingLimits(max_packets_in_ring=5000,
                                       max_window_packets=5000))
    for f in range(frames):
        for p in range(per_frame // 200):
            t = float(batch.device_times[(f * per_frame) + p * 200])
            ring.push(frame_index=f, local_index=p, device_time=t, capture_time=t)
    return ring.select(duration_s, end_frame_index=frames - 1)


def run_scenario(name: str, seed: int, cfg, duration_s: float = 0.3) -> dict:
    scene = build_scenario(name, seed)
    selected = _selected_window(scene.batch, duration_s)
    inputs = WindowInputs(batch=scene.batch, selected_window=selected,
                          source_window_id=f"synth:{name}", config=cfg,
                          manual_roi_aabb_m=tuple(scene.roi_aabb_m),
                          window_end_device_time_s=selected.end_device_time,
                          availability="available")
    result = run_detection_window(inputs)
    candidate = result.candidate_line or {}
    error = None
    truth = scene.truth
    truth_segments = {key: value for key, value in truth.items()
                      if isinstance(value, dict) and "endpoints" in value}
    if candidate and truth_segments.get("wire"):
        direction = np.asarray(candidate["direction"], dtype=float)
        p0, p1 = (np.asarray(p, dtype=float)
                  for p in truth_segments["wire"]["endpoints"])
        truth_dir = p1 - p0
        truth_dir = truth_dir / np.linalg.norm(truth_dir)
        dot = float(abs(direction @ truth_dir))
        error = {"direction_dot_abs": dot,
                 "direction_angle_deg": float(np.degrees(np.arccos(min(1.0, dot))))}
        if candidate["segment_endpoints_sensor_m"]:
            meas = np.asarray(candidate["segment_endpoints_sensor_m"], dtype=float)
            span_truth = float(np.linalg.norm(p1 - p0))
            span_meas = float(np.linalg.norm(meas[1] - meas[0]))
            error["span_truth_m"] = span_truth
            error["span_meas_m"] = span_meas
            error["span_error_m"] = abs(span_meas - span_truth)
    expect = scene.expect
    checks = {}
    if "status_in" in expect:
        checks["status_in"] = result.status in expect["status_in"]
    if "status_not_in" in expect:
        checks["status_not_in"] = result.status not in expect["status_not_in"]
    if "reject_if_unknown" in expect and result.status != "MEASURED_CURRENT":
        expected_code = expect["reject_if_unknown"]
        reason = result.reject_reason or ""
        checks["reject_reason"] = expected_code in reason
    passed = all(checks.values()) if checks else None
    return {
        "scenario": name, "seed": seed, "status": result.status,
        "geometry_class": result.geometry_class,
        "first_blocking_stage": result.first_blocking_stage,
        "reject_reason": result.reject_reason,
        "window_quality": selected.time_quality,
        "window_packet_count": selected.packet_count,
        "window_partial": selected.partial_window,
        "candidate": candidate,
        "error_vs_truth": error,
        "behaviour_checks": checks,
        "behaviour_pass": passed,
        "data_label": "SYNTHETIC",
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--scenario", action="append", default=None,
                        help="scenario name; repeatable; default = all")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--duration", type=float, default=0.3)
    parser.add_argument("--strict", action="store_true",
                        help="exit nonzero when any behaviour check fails")
    args = parser.parse_args(argv)

    names = args.scenario or list(SCENARIO_BUILDERS)
    unknown = [name for name in names if name not in SCENARIO_BUILDERS]
    if unknown:
        raise SystemExit(f"unknown scenarios: {unknown}; available: {sorted(SCENARIO_BUILDERS)}")
    output: Path = args.output
    output.mkdir(parents=True, exist_ok=False)

    cfg = load_wire_config()
    rows = [run_scenario(name, args.seed + i * 1000, cfg, args.duration)
            for i, name in enumerate(names)]
    with (output / "scenarios.jsonl").open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    manifest = {
        "schema_version": "na70-wire-synthetic-run-v1",
        "data_label": "SYNTHETIC",
        "warning": ("Synthetic evidence tests code behaviour only; it is not"
                    " NA70 wire-detection capability evidence."),
        "seed_base": args.seed,
        "duration_s": args.duration,
        "config_sha256": hashlib.sha256(
            json.dumps(cfg.to_dict(), sort_keys=True).encode()).hexdigest(),
        "command": [sys.executable, *sys.argv],
        "environment": {"python": sys.version, "platform": platform.platform(),
                        "numpy": np.__version__},
        "source_hashes": {
            rel: sha256_file(ROOT / rel) for rel in (
                "packages/na70_core/na70_core/wire/synthetic.py",
                "packages/na70_core/na70_core/wire/pipeline.py",
                "packages/na70_core/na70_core/wire/fitting.py",
                "packages/na70_core/na70_core/wire/validation.py",
                "packages/na70_core/na70_core/wire/proposals.py",
                "packages/na70_core/na70_core/windows.py",
                "packages/na70_core/na70_core/observations.py",
                "packages/na70_core/na70_core/wire_default.yaml",
                "tools/run_wire_synthetic_matrix.py",
            )},
        "scenarios": [{k: row[k] for k in ("scenario", "status", "first_blocking_stage",
                                           "reject_reason", "behaviour_pass")}
                      for row in rows],
        "behaviour_pass_count": sum(1 for row in rows if row["behaviour_pass"]),
        "scenario_count": len(rows),
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), "utf-8")

    lines = ["# 合成场景矩阵报告（SYNTHETIC）", "",
             "本目录全部为 SYNTHETIC 证据，只验证代码行为；不构成 NA70 真实导线检出能力证据。",
             "",
             f"seed_base={args.seed}，duration={args.duration}s，共 {len(rows)} 个场景。",
             "", "| 场景 | 状态 | 首阻断阶段 | 拒绝原因 | 行为检查 |",
             "|---|---|---|---|---|"]
    for row in rows:
        verdict = {True: "PASS", False: "FAIL", None: "n/a"}[row["behaviour_pass"]]
        lines.append(f"| {row['scenario']} | {row['status']} | "
                     f"{row['first_blocking_stage'] or '-'} | "
                     f"{row['reject_reason'] or '-'} | {verdict} |")
    lines += ["", "逐场景详情见 scenarios.jsonl（含候选几何、与真值的方向/跨度误差、窗口质量）。", ""]
    (output / "REPORT.md").write_text("\n".join(lines), "utf-8")

    failed = [row["scenario"] for row in rows if row["behaviour_pass"] is False]
    print(json.dumps({"output": str(output), "scenarios": len(rows),
                      "failed": failed, "data_label": "SYNTHETIC"}, ensure_ascii=False))
    if args.strict and failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
