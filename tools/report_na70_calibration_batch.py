"""Process a captured calibration batch locally (no WSL).

Reads the batch manifest written by ``capture_na70_calibration_batch.ps1`` and
runs the static/Allan, six-face and plane-rotation analyses.  Outputs one
summary plus per-stage JSON.  Translation is never filled: it must be measured.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "tools/lio/static_check.yaml"


def load_tool(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def session_paths(entry):
    local = Path(entry["local"])
    return local / "imu_frames.jsonl", local / "raw/lidar.pcap"


def process(batch_path, output, *, config_path=DEFAULT_CONFIG, max_range=20.0,
            frame_step=5, max_offset=0.2, step=0.01):
    batch = json.loads(Path(batch_path).read_text(encoding="utf-8"))
    sessions = batch.get("sessions") or {}
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    result = {"schema": "na70-calibration-batch-report-v1",
              "batch": str(batch_path), "stages": {}, "translation": None,
              "claims": {"extrinsic_frozen": False, "time_sync_validated": False}}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)

    static_entry = sessions.get("STATIC")
    if static_entry:
        static_tool = load_tool("batch_static", "analyze_na70_imu_run.py")
        frames, _ = session_paths(static_entry)
        static = static_tool.analyse(frames, config)
        (output / "static.json").write_text(
            json.dumps(static, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8")
        result["stages"]["static"] = {"status": static.get("status"),
                                      "allan": (static.get("allan") or {}).get("status")}
    else:
        result["stages"]["static"] = {"status": "NOT_PROVIDED"}

    faces_entry = sessions.get("FACES")
    if faces_entry:
        face_tool = load_tool("batch_faces", "solve_na70_six_face_sessions.py")
        faces = face_tool.evaluate(batch_path, config)
        (output / "six-face.json").write_text(
            json.dumps(faces, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8")
        result["stages"]["six_face"] = {"status": faces.get("status"),
                                        "reasons": faces.get("reasons")}
    else:
        result["stages"]["six_face"] = {"status": "NOT_PROVIDED"}

    rotation_entry = sessions.get("ROT")
    if rotation_entry:
        frames, pcap = session_paths(rotation_entry)
        if not pcap.is_file():
            result["stages"]["rotation"] = {"status": "MISSING_PCAP", "pcap": str(pcap)}
        else:
            rotation_tool = load_tool("batch_rotation", "calibrate_na70_rotation_planes.py")
            try:
                rotation = rotation_tool.calibrate(
                    pcap, frames, max_range=max_range, frame_step=frame_step,
                    max_offset=max_offset, step=step)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                rotation = {"status": "INVALID_DATA", "error": str(exc)}
            (output / "rotation.json").write_text(
                json.dumps(rotation, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                encoding="utf-8")
            result["stages"]["rotation"] = {
                "status": rotation.get("status"),
                "time_offset_s": rotation.get("time_offset_s"),
                "residual": (rotation.get("solution") or {}).get("residual"),
                "observability": (rotation.get("solution") or {}).get("observability"),
            }
            if rotation.get("status") == "SOLVED_CANDIDATE":
                result["R_imu_lidar"] = rotation["solution"]["R_imu_lidar"]
                result["time_offset_s"] = rotation.get("time_offset_s")
    else:
        result["stages"]["rotation"] = {"status": "NOT_PROVIDED"}

    statuses = [stage.get("status") for stage in result["stages"].values()]
    if any(status == "INVALID_DATA" for status in statuses):
        result["status"] = "INVALID"
    elif result["stages"]["rotation"].get("status") == "SOLVED_CANDIDATE":
        result["status"] = "ROTATION_READY_TRANSLATION_PENDING"
    else:
        result["status"] = "PARTIAL"
    result["next"] = ("measure t_IL from the LiDAR optical origin to the IMU origin; "
                      "then compose a calibration candidate and run hold-out validation")
    (output / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8")
    lines = ["# 标定批次本地处理", "",
             f"- 结论：**{result['status']}**",
             f"- 静置：{result['stages']['static'].get('status')}",
             f"- 六面：{result['stages']['six_face'].get('status')}",
             f"- 旋转/时间偏移：{result['stages']['rotation'].get('status')}"]
    if result.get("R_imu_lidar"):
        lines += [f"- R_IL：{result['R_imu_lidar']}",
                  f"- 时间偏移：{result.get('time_offset_s')} s"]
    lines += ["", "平移 t_IL 未填写；必须先测量光学原点到 IMU 原点。",
              "本报告不是冻结标定，仍需留出转动/墙面验证。"]
    (output / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--max-range", type=float, default=20.0)
    parser.add_argument("--frame-step", type=int, default=5)
    parser.add_argument("--max-offset", type=float, default=0.2)
    parser.add_argument("--step", type=float, default=0.01)
    args = parser.parse_args(argv)
    try:
        result = process(args.batch, args.output, config_path=args.config,
                         max_range=args.max_range, frame_step=args.frame_step,
                         max_offset=args.max_offset, step=args.step)
    except (OSError, ValueError, TypeError, KeyError, FileExistsError) as exc:
        print(json.dumps({"status": "INVALID_DATA", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": result["status"], "output": str(args.output)},
                     ensure_ascii=False))
    return 0 if result["status"] in ("ROTATION_READY_TRANSLATION_PENDING", "PARTIAL") else 2


if __name__ == "__main__":
    raise SystemExit(main())