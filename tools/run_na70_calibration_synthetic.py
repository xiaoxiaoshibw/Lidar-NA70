"""SYNTHETIC end-to-end check of the calibration math and its error reporting.

Generates datasets with known answers, runs the same solvers the real tools use,
and records recovery errors.  This is a math regression, not a hardware or
field-calibration result.
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
from na70_core.calibration import planes, rotation, six_face  # noqa: E402
from na70_core.calibration import synthetic  # noqa: E402
from na70_core.calibration.extrinsic import compose_candidate  # noqa: E402

TOLERANCES = {
    "six_face_matrix_max_abs": 0.01,
    "six_face_bias_max_abs_m_s2": 0.01,
    "rotation_angle_deg": 0.2,
    "time_offset_s": 0.0015,
    "plane_distance_error_m": 0.01,
    "range_slope_error": 0.005,
}


def rotation_angle_deg(a, b):
    value = (np.trace(np.asarray(a).T @ np.asarray(b)) - 1.0) / 2.0
    return math.degrees(math.acos(max(-1.0, min(1.0, value))))


def run(output):
    output.mkdir(parents=True, exist_ok=False)

    matrix = np.array([[1.02, 0.008, -0.004],
                       [0.003, 0.985, 0.006],
                       [-0.002, 0.004, 1.011]])
    bias = np.array([0.12, -0.07, 0.05])
    six_data = synthetic.six_face_dataset(matrix=matrix, bias=bias, noise_m_s2=0.002, seed=5)
    six = six_face.solve(six_data["measurements_m_s2"], six_data["directions"])
    six_error = float(np.max(np.abs(np.array(six["transform"]["M"]) - matrix)))
    bias_error = float(np.max(np.abs(np.array(six["transform"]["bias_m_s2"]) - bias)))

    rotation_data = synthetic.rotation_dataset(time_offset_s=0.013, noise_rad_s=0.01, seed=9)
    rot = rotation.solve_lidar_imu_rotation(
        rotation_data["t_lidar"], rotation_data["omega_lidar"],
        rotation_data["t_imu"], rotation_data["omega_imu"])
    angle_error = rotation_angle_deg(rotation_data["truth"]["R_imu_lidar"],
                                     np.array(rot["R_imu_lidar"]))
    offset_error = abs(rot["time_offset_s"] - rotation_data["truth"]["time_offset_s"])

    reference = [5.0, 10.0, 20.0]
    measured = []
    plane_reports = []
    for index, distance in enumerate(reference):
        data = synthetic.plane_dataset(distance_m=distance, tilt_deg=1.5 + index,
                                       noise_m=0.004, seed=20 + index)
        report = planes.plane_measurement(data["points"], reference_distance_m=distance)
        measured.append(report["normal_distance_m"])
        plane_reports.append({"reference_distance_m": distance,
                              "measured_distance_m": report["normal_distance_m"],
                              "error_m": report["reference"]["error_m"],
                              "residual_rms_m": report["residual_rms_m"]})
    range_fit = planes.multi_range_fit(reference, measured)

    translation = np.array([0.043, 0.0, -0.060])
    candidate = compose_candidate(rot, translation, translation_uncertainty_m=0.002,
                                  source_note="SYNTHETIC mechanical lever arm",
                                  rotation_source="run_na70_calibration_synthetic")
    inverse_check = float(np.max(np.abs(
        np.asarray(candidate["T_lidar_imu"]) @ np.asarray(candidate["T_imu_lidar"]) - np.eye(4))))

    summary = {
        "schema": "na70-calibration-synthetic-v1",
        "evidence_kind": "SYNTHETIC",
        "six_face": {"status": six["status"], "matrix_max_abs_error": six_error,
                     "bias_max_abs_error_m_s2": bias_error,
                     "residual_rms_m_s2": six["residual"]["rms_m_s2"]},
        "rotation": {"status": rot["status"], "angle_error_deg": angle_error,
                     "time_offset_error_s": offset_error,
                     "relative_residual": rot["residual"]["relative"],
                     "observability": rot["observability"]["status"]},
        "planes": {"status": range_fit["status"], "points": plane_reports,
                   "range_slope": range_fit["slope"],
                   "range_intercept_m": range_fit["intercept_m"],
                   "slope_error": abs(range_fit["slope"] - 1.0)},
        "candidate": {"status": candidate["status"], "inverse_error": inverse_check,
                      "translation_m": candidate["t_imu_lidar_m"],
                      "time_offset_s": candidate["time_offset_s"]},
        "tolerances": TOLERANCES,
    }
    checks = {
        "six_face_matrix": six_error <= TOLERANCES["six_face_matrix_max_abs"],
        "six_face_bias": bias_error <= TOLERANCES["six_face_bias_max_abs_m_s2"],
        "rotation_angle": angle_error <= TOLERANCES["rotation_angle_deg"],
        "time_offset": offset_error <= TOLERANCES["time_offset_s"],
        "plane_distance": max(abs(item["error_m"]) for item in plane_reports)
        <= TOLERANCES["plane_distance_error_m"],
        "range_slope": abs(range_fit["slope"] - 1.0) <= TOLERANCES["range_slope_error"],
        "candidate_inverse": inverse_check < 1e-12,
    }
    summary["checks"] = checks
    summary["status"] = "SYNTHETIC_PASS" if all(checks.values()) else "SYNTHETIC_FAIL"
    summary["claims"] = {"hardware_validated": False, "field_calibration": False,
                         "time_sync_validated": False}

    def save(name, payload):
        (output / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2,
                                              allow_nan=False) + "\n", encoding="utf-8")

    save("six-face.json", six)
    save("rotation.json", rot)
    save("planes.json", {"planes": plane_reports, "range_fit": range_fit})
    save("candidate.json", candidate)
    save("summary.json", summary)
    lines = ["# 标定数学 SYNTHETIC 验证", "",
             "本证据为合成数据回归，不是 NA70/YESENSE 现场标定结果。", "",
             f"- 六面：M 最大误差 {six_error:.2e}，bias 最大误差 {bias_error:.2e} m/s²",
             f"- 旋转：角度误差 {angle_error:.4f}°，时间偏移误差 {offset_error * 1000:.3f} ms",
             f"- 平面：法向距离最大误差 "
             f"{max(abs(item['error_m']) for item in plane_reports):.2e} m，"
             f"距离拟合斜率 {range_fit['slope']:.6f}",
             f"- 外参候选：{candidate['status']}，正逆矩阵最大误差 {inverse_check:.2e}",
             "", f"结论：**{summary['status']}**", ""]
    (output / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    summary = run(args.output)
    print(json.dumps({"status": summary["status"], "checks": summary["checks"]}, ensure_ascii=False))
    return 0 if summary["status"] == "SYNTHETIC_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())