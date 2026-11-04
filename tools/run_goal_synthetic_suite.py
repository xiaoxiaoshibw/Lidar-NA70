# -*- coding: utf-8 -*-
"""Run deterministic SYNTHETIC goal scenarios and save truth-aware metrics."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))

from backend.goal_detection.config import configuration_version, load_configuration  # noqa: E402
from backend.goal_detection.pipeline import GoalDemoPipeline  # noqa: E402
from backend.goal_detection.synthetic import (SCENARIOS, generate_sequence,
                                               scenario_manifest)  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_hashes() -> dict:
    paths = [Path(__file__), ROOT / "web/backend/goal_detection.yaml"]
    paths.extend(sorted((ROOT / "web/backend/goal_detection").glob("*.py")))
    return {str(path.relative_to(ROOT)).replace("\\", "/"): _sha256(path)
            for path in paths}


def evaluate_frames(rows: list[dict], truths: list[dict], hit_radius: float = 1.5) -> dict:
    hits = misses = false_outputs = visible_frames = 0
    errors, width_errors, height_errors = [], [], []
    matched_ids = []
    hit_flags = []
    false_confirmed_frames = 0
    absence_measurement_frames = 0
    for row, truth in zip(rows, truths):
        measurement = row.get("selected_measurement")
        center = measurement.get("center") if measurement else None
        if truth["visible"]:
            visible_frames += 1
            if center:
                error = float(np.linalg.norm(np.asarray(center[:2])
                                             - np.asarray(truth["center"][:2])))
                if error <= hit_radius:
                    hits += 1
                    errors.append(error)
                    if measurement.get("size") and truth.get("size"):
                        width_errors.append(abs(float(measurement["size"][1])
                                                - float(truth["size"][1])))
                        height_errors.append(abs(float(measurement["size"][2])
                                                 - float(truth["size"][2])))
                    hit_flags.append(True)
                    goal_id = row.get("goal", {}).get("id", 0)
                    if goal_id:
                        matched_ids.append(goal_id)
                else:
                    misses += 1
                    false_outputs += 1
                    hit_flags.append(False)
            else:
                misses += 1
                hit_flags.append(False)
        elif center:
            false_outputs += 1
            hit_flags.append(False)
        else:
            hit_flags.append(False)
        if not truth["visible"] and row.get("goal", {}).get("state") in ("LOCKED", "TRACKING") \
                and row.get("goal", {}).get("has_measurement", False):
            false_confirmed_frames += 1
        if not truth["visible"] and (row.get("selected_measurement") is not None
                                      or row.get("goal", {}).get("has_measurement", False)):
            absence_measurement_frames += 1
    positives = hits + false_outputs
    id_switches = sum(a != b for a, b in zip(matched_ids, matched_ids[1:]))
    longest = current = 0
    for hit in hit_flags:
        current = current + 1 if hit else 0
        longest = max(longest, current)
    return {
        "visible_frames": visible_frames, "hits": hits, "misses": misses,
        "false_outputs": false_outputs,
        "precision": round(hits / positives, 4) if positives else None,
        "recall": round(hits / visible_frames, 4) if visible_frames else None,
        "center_error_p95_m": (round(float(np.percentile(errors, 95)), 4)
                               if errors else None),
        "width_error_p95_m": (round(float(np.percentile(width_errors, 95)), 4)
                              if width_errors else None),
        "height_error_p95_m": (round(float(np.percentile(height_errors, 95)), 4)
                               if height_errors else None),
        "id_switches": id_switches,
        "matched_track_ids": sorted(set(matched_ids)),
        "longest_hit_streak": longest,
        "false_confirmed_frames_while_absent": false_confirmed_frames,
        "absence_measurement_frames": absence_measurement_frames,
    }


def run_case(scenario: str, seed: int, frames: int, cfg: dict) -> dict:
    sequence = generate_sequence(scenario, seed, frames)
    pipeline = GoalDemoPipeline(cfg)
    rows, truths = [], []
    input_digest = hashlib.sha256()
    for item in sequence:
        truth = item.truth.as_dict()
        input_digest.update(np.ascontiguousarray(item.points).tobytes())
        input_digest.update(json.dumps(truth, sort_keys=True,
                                       separators=(",", ":")).encode("utf-8"))
        result = pipeline.process_frame(item.points, truth["frame_index"], truth["device_time"])
        measurement = result.get("selected_measurement")
        failure_reason = None
        if truth["visible"] and measurement is None:
            association = result.get("association") or {}
            failure_reason = association.get("reject_reason")
            if failure_reason in (None, "no_candidate"):
                traces = result.get("execution_trace", {}).get("accumulated", [])
                for stage in reversed(traces):
                    if stage.get("reason_counts"):
                        failure_reason = next(iter(stage["reason_counts"]))
                        break
            failure_reason = failure_reason or "no_measurement"
        rows.append({"frame_index": truth["frame_index"],
                     "device_time": truth["device_time"],
                     "selected_measurement": measurement,
                     "accumulated_best": (result.get("accumulated_candidates") or [None])[0],
                     "candidate_evidence": {
                         "single": result.get("single_candidates", []),
                         "accumulated": result.get("accumulated_candidates", []),
                         "selected": measurement,
                     },
                     "time_quality": result.get("time_quality", (result.get("goal") or {}).get("time_quality")),
                     "association": result.get("association"),
                     "execution_trace": result.get("execution_trace"),
                     "failure_reason": failure_reason,
                     "goal": result.get("goal", {})})
        truths.append(truth)
    return {"scenario": scenario, "seed": seed, "frames": frames,
            "input_sha256": input_digest.hexdigest(),
            "metrics": evaluate_frames(rows, truths), "truth": truths, "rows": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--scenario", action="append", choices=SCENARIOS)
    parser.add_argument("--seed", action="append", type=int)
    parser.add_argument("--frames", type=int, default=200)
    args = parser.parse_args()
    scenarios = args.scenario or list(SCENARIOS)
    seeds = args.seed or [0]
    cfg = load_configuration()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    cases = []
    for scenario in scenarios:
        for seed in seeds:
            case = run_case(scenario, seed, args.frames, cfg)
            case_path = output / f"{scenario}-seed{seed}.json"
            case_path.write_text(json.dumps(case, ensure_ascii=False), "utf-8")
            cases.append({key: case[key] for key in (
                "scenario", "seed", "frames", "input_sha256", "metrics")})
            print(f"{scenario} seed={seed}: {case['metrics']}")
    summary = {**scenario_manifest(), "config_version": configuration_version(cfg),
               "env": {"python": sys.version.split()[0],
                       "platform": platform.platform()},
               "source_hashes": _source_hashes(),
               "cases": cases}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), "utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
