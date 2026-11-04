"""W4-A proposal comparison runner.

Runs the three proposal baselines (angular-slab connected components,
forward-projection HoughLinesP, neighbourhood-PCA linearity) over the
synthetic scenario set (deterministic seeds) and, on request, over a real
static PCAP window. Output is a per-(scenario, method) row with proposal
count, candidate status, and - for synthetic scenes with wire truth - the
direction/span error of the best recovered line.

All synthetic artifacts are tagged SYNTHETIC, real PCAP rows are tagged
REAL_PCAP_UNKNOWN_LINE; none of this enters a TP denominator without
reviewed truth.
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

from backend import decoder, thin_wire  # noqa: E402
from na70_core import RayBatch  # noqa: E402
from na70_core.wire import (FullFovConfig, HoughConfig, PcaConfig, WindowInputs,  # noqa: E402
                            build_scenario, load_wire_config, run_detection_window)
from na70_core.wire import (propose_full_fov, propose_hough, propose_pca)  # noqa: E402
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
    ring = PacketWindowRing(RingLimits(max_packets_in_ring=5000, max_window_packets=5000))
    for f in range(frames):
        for p in range(per_frame // 200):
            t = float(batch.device_times[f * per_frame + p * 200])
            ring.push(frame_index=f, local_index=p, device_time=t, capture_time=t)
    return ring.select(duration_s, end_frame_index=frames - 1)


def _proposals(method: str, batch, fov_cfg, hough_cfg, pca_cfg):
    if method == "slab":
        return propose_full_fov(batch, fov_cfg), None
    if method == "hough":
        return propose_hough(batch, hough_cfg), None
    if method == "pca":
        props, report = propose_pca(batch, pca_cfg)
        return props, {"insufficient_neighbourhood": report.insufficient_neighbourhood,
                       "below_linearity": report.below_linearity,
                       "below_extent": report.below_extent,
                       "merged_into_existing": report.merged_into_existing}
    raise ValueError(f"unknown method {method}")


def _direction_span_error(candidate, truth_endpoints):
    if not candidate or not truth_endpoints:
        return None
    direction = np.asarray(candidate["direction"], dtype=float)
    p0, p1 = (np.asarray(p, dtype=float) for p in truth_endpoints)
    truth_dir = p1 - p0
    norm = np.linalg.norm(truth_dir)
    if norm == 0:
        return None
    truth_dir = truth_dir / norm
    dot = float(abs(direction @ truth_dir))
    out = {"direction_dot_abs": dot,
           "direction_angle_deg": float(np.degrees(np.arccos(min(1.0, dot)))),
           "span_truth_m": float(norm)}
    if candidate["segment_endpoints_sensor_m"]:
        seg = np.asarray(candidate["segment_endpoints_sensor_m"], dtype=float)
        out["span_meas_m"] = float(np.linalg.norm(seg[1] - seg[0]))
        out["span_error_m"] = abs(out["span_meas_m"] - float(norm))
    return out


def _run_row(label, batch, manual_roi, truth_endpoints, method, wire_cfg,
             fov_cfg, hough_cfg, pca_cfg, selected, data_label):
    proposals, extra = _proposals(method, batch, fov_cfg, hough_cfg, pca_cfg)
    proposal_count = len(proposals)
    best_status = "NO_PROPOSAL" if proposal_count == 0 else None
    best_out = None
    best_reason = None
    best_block = None
    from na70_core.wire.fitting import fit_line_ransac
    from na70_core.wire.validation import evaluate_finite_support
    candidates = []
    for proposal in proposals:
        fit = fit_line_ransac(batch, proposal, wire_cfg.fit)
        if not fit.ok:
            continue
        support = evaluate_finite_support(batch, fit, wire_cfg.validation,
                                          selected.end_device_time)
        candidates.append((proposal, fit, support))
    scored = [c for c in candidates if c[2].ok]
    if scored:
        # pick best span (longest measured-support line)
        best = max(scored, key=lambda c: c[2].span_m)
        proposal, fit, support = best
        candidate = {
            "direction": [float(v) for v in fit.direction],
            "point_on_line": [float(v) for v in fit.point_on_line],
            "segment_endpoints_sensor_m": list(support.segment_sensor) if support.segment_sensor else None,
            "nearest_distance_m": support.nearest_distance_m,
            "unique_packet_count": support.unique_packet_count,
            "span_m": support.span_m,
            "coverage_fraction": support.coverage_fraction,
            "largest_internal_gap_m": support.largest_internal_gap_m,
            "rms_orthogonal_m": support.rms_orthogonal_m,
        }
        best_status = "MEASURED_CURRENT"
        best_reason = None
        best_block = None
    else:
        candidate = None
        if candidates:
            best_status = "UNKNOWN"
            best_block = "finite_support"
            rejection_counts: dict[str, int] = {}
            for _prop, _fit, sup in candidates:
                for code in (sup.reject_reason or "").split(";"):
                    if code:
                        rejection_counts[code] = rejection_counts.get(code, 0) + 1
            best_reason = ";".join(f"{k}x{v}" for k, v in sorted(rejection_counts.items()))
        else:
            best_status = "NO_PROPOSAL"
            best_block = None
            best_reason = None
    row = {
        "label": label, "method": method, "data_label": data_label,
        "proposal_count": proposal_count,
        "evaluated_count": len(candidates),
        "status": best_status, "first_blocking_stage": best_block,
        "reject_reason": best_reason,
        "error_vs_truth": _direction_span_error(candidate, truth_endpoints),
        "candidate_span_m": candidate["span_m"] if candidate else None,
        "candidate_nearest_m": candidate["nearest_distance_m"] if candidate else None,
    }
    if extra:
        row["method_extra"] = extra
    return row


def decode_real_window(pcap: Path, end_frame: int, duration_s: float,
                       install_direction: int):
    scan = decoder.scan_pcap(pcap)
    cfg_w1 = thin_wire.load_config()
    packets = thin_wire.packet_timeline(scan, cfg_w1)
    ring = PacketWindowRing(RingLimits(
        max_packets_in_ring=max(2000, len(packets) + 64),
        max_window_packets=max(5000, 100 * len(scan.frames)),
        time_epsilon_s=cfg_w1["time_epsilon_s"],
        max_packet_gap_s=cfg_w1["max_packet_gap_s"]))
    for packet in packets:
        ring.push(frame_index=packet.frame_index, local_index=packet.local_index,
                  device_time=packet.device_time, capture_time=packet.capture_time)
    selected = ring.select(duration_s, end_frame_index=end_frame)
    grouped = {}
    for p in selected.packets:
        grouped.setdefault(p.frame_index, []).append(p)
    bufs = {k: [] for k in ("codes", "points", "fi", "pi", "pti", "dt", "ct")}
    with pcap.open("rb") as src:
        for fi, parts in sorted(grouped.items()):
            fr = scan.frames[fi]
            if not fr["complete"]:
                del grouped[fi]
                continue
            pts = decoder.decode_frame(pcap, fr, install_direction, source=src)
            codes = decoder.decode_frame_codes(pcap, fr, source=src)
            rid = np.concatenate([np.arange(p.local_index * 200, (p.local_index + 1) * 200)
                                  for p in parts])
            bufs["codes"].append(codes[rid])
            bufs["points"].append(pts[rid])
            bufs["fi"].append(np.full(rid.size, fi, dtype=np.int64))
            bufs["pi"].append(np.repeat(np.array([p.index for p in parts], dtype=np.int64), 200))
            bufs["pti"].append(rid)
            bufs["dt"].append(np.repeat(np.array([p.device_time for p in parts]), 200))
            bufs["ct"].append(np.repeat(np.array([p.capture_time for p in parts]), 200))
    batch = RayBatch.from_decoded(
        capture_sha256=sha256_file(pcap), coordinate_frame="lidar_link",
        install_direction=install_direction, codes=np.concatenate(bufs["codes"]),
        points=np.concatenate(bufs["points"]), frame_indices=np.concatenate(bufs["fi"]),
        packet_indices=np.concatenate(bufs["pi"]), point_indices=np.concatenate(bufs["pti"]),
        device_times=np.concatenate(bufs["dt"]), capture_times=np.concatenate(bufs["ct"]))
    return scan, selected, batch


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--duration", type=float, default=0.3)
    parser.add_argument("--methods", default="slab,hough,pca")
    parser.add_argument("--scenario", action="append", default=None)
    parser.add_argument("--real-pcap", type=Path, default=None)
    parser.add_argument("--real-end-frame", type=int, default=None)
    parser.add_argument("--real-duration", type=float, default=1.0)
    parser.add_argument("--install-direction", type=int, default=1, choices=[1, 2])
    args = parser.parse_args(argv)

    methods = [m.strip() for m in args.methods.split(",") if m.strip()]
    for m in methods:
        if m not in ("slab", "hough", "pca"):
            raise SystemExit(f"unknown method {m}")
    output: Path = args.output
    output.mkdir(parents=True, exist_ok=False)

    wire_cfg = load_wire_config()
    fov_cfg = FullFovConfig()
    hough_cfg = HoughConfig()
    pca_cfg = PcaConfig()
    rows = []

    names = args.scenario or []
    for i, name in enumerate(names):
        scene = build_scenario(name, args.seed + i * 1000)
        selected = _selected_window(scene.batch, args.duration)
        truth = scene.truth.get("wire") or {}
        endpoints = truth.get("endpoints")
        for method in methods:
            rows.append(_run_row(name, scene.batch, scene.roi_aabb_m, endpoints,
                                 method, wire_cfg, fov_cfg, hough_cfg, pca_cfg,
                                 selected, "SYNTHETIC"))

    if args.real_pcap is not None and args.real_end_frame is not None:
        scan, selected, batch = decode_real_window(
            args.real_pcap, args.real_end_frame, args.real_duration,
            args.install_direction)
        for method in methods:
            rows.append(_run_row(
                f"real:{args.real_pcap.name}:f{args.real_end_frame}",
                batch, None, None, method, wire_cfg, fov_cfg, hough_cfg, pca_cfg,
                selected, "REAL_PCAP_UNKNOWN_LINE"))

    with (output / "compare.jsonl").open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    manifest = {
        "schema_version": "na70-wire-w4a-compare-v1",
        "command": [sys.executable, *sys.argv],
        "seed": args.seed, "duration_s": args.duration,
        "methods": methods, "scenarios": names,
        "real_inputs": ({"pcap": str(args.real_pcap), "end_frame": args.real_end_frame,
                         "duration_s": args.real_duration,
                         "install_direction": args.install_direction}
                        if args.real_pcap else None),
        "environment": {"python": sys.version, "platform": platform.platform(),
                        "numpy": np.__version__},
        "source_hashes": {
            rel: sha256_file(ROOT / rel) for rel in (
                "packages/na70_core/na70_core/wire/pipeline.py",
                "packages/na70_core/na70_core/wire/proposals.py",
                "packages/na70_core/na70_core/wire/proposal_full_fov.py",
                "packages/na70_core/na70_core/wire/proposal_hough.py",
                "packages/na70_core/na70_core/wire/proposal_pca.py",
                "packages/na70_core/na70_core/wire/fitting.py",
                "packages/na70_core/na70_core/wire/validation.py",
                "packages/na70_core/na70_core/wire/constants.py",
                "packages/na70_core/na70_core/wire/config.py",
                "packages/na70_core/na70_core/wire_default.yaml",
                "packages/na70_core/na70_core/windows.py",
                "packages/na70_core/na70_core/observations.py",
                "packages/na70_core/na70_core/wire/synthetic.py",
                "tools/run_wire_w4_proposal_compare.py",
                "web/backend/decoder.py", "web/backend/thin_wire.py",
            )},
        "data_labels": sorted({row["data_label"] for row in rows}),
        "warning": ("SYNTHETIC rows are code-behaviour evidence only; real PCAP"
                    " rows are REAL_PCAP_UNKNOWN_LINE, not HV-line identity."),
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), "utf-8")

    lines = ["# W4-A 提案对照（SYNTHETIC + REAL_PCAP_UNKNOWN_LINE）", "",
             "| label | method | proposals | status | 首阻断 | span_m | dir_err_deg | span_err_m |",
             "|---|---|---:|---|---|---:|---:|---:|"]
    for row in rows:
        err = row.get("error_vs_truth") or {}
        lines.append(f"| {row['label']} | {row['method']} | {row['proposal_count']} | "
                     f"{row['status']} | {row['first_blocking_stage'] or '-'} | "
                     f"{row['candidate_span_m'] if row['candidate_span_m'] is not None else '-'} | "
                     f"{round(err.get('direction_angle_deg', 0),3) if err else '-'} | "
                     f"{round(err.get('span_error_m', 0),3) if err and 'span_error_m' in err else '-'} |")
    (output / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "rows": len(rows),
                      "labels": sorted({r['label'] for r in rows})}, ensure_ascii=False))


if __name__ == "__main__":
    main()
