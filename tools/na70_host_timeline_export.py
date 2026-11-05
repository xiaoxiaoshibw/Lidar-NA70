"""Export a standalone diagnostic host-receive timeline (not a SLAM map)."""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))
from backend import decoder  # noqa: E402


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as source:
        for part in iter(lambda: source.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def encode(array):
    return base64.b64encode(array.tobytes()).decode("ascii")


def load_model(path):
    """Read the repository's approximate assembly OBJ for offline display."""
    vertices, faces, group = [], [], ""
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if not fields:
            continue
        if fields[0] == "g":
            group = fields[1]
        elif fields[0] == "v":
            vertices.append([float(x) for x in fields[1:4]])
        elif fields[0] == "f":
            faces.append({"group": group, "v": [int(x.split('/')[0]) - 1 for x in fields[1:]]})
    if not vertices or not faces:
        raise ValueError("empty approximate assembly model")
    return {"vertices": vertices, "faces": faces}


def export(session, trajectory, output, *, points_per_frame=1200):
    session, trajectory, output = Path(session), Path(trajectory), Path(output)
    pcap, imu = session / "raw/lidar.pcap", session / "imu_frames.jsonl"
    if points_per_frame < 1:
        raise ValueError("points_per_frame must be positive")
    scan = decoder.scan_pcap(pcap)
    origin = float(scan.frames[0]["packet_capture_times"][0])
    frames = []
    with pcap.open("rb") as source:
        for frame in scan.frames:
            points = decoder.decode_frame(pcap, frame, 1, source=source)
            codes = decoder.decode_frame_codes(pcap, frame, source=source)
            # Diagnostic display mask only; 0xFFFF semantics are unverified.
            selected = np.flatnonzero((codes[:, 0] != 0) & (codes[:, 0] != 65535)
                                      & (codes[:, 1] != 65535) & (codes[:, 2] != 65535))
            eligible = len(selected)
            if len(selected) > points_per_frame:
                selected = selected[np.linspace(0, len(selected) - 1, points_per_frame, dtype=np.int64)]
            frames.append({"index": int(frame["index"]), "start": float(frame["packet_capture_times"][0] - origin),
                           "end": float(frame["packet_capture_times"][-1] - origin),
                           "complete": bool(frame["complete"]), "rays": int(len(codes)),
                           "eligible": eligible, "count": int(len(selected)),
                           "xyzi": encode(np.asarray(points[selected], dtype="<f4")),
                           "source_indices": encode(np.asarray(selected, dtype="<u4"))})
    batches = []
    with imu.open(encoding="utf-8") as source:
        for line in source:
            row = json.loads(line)
            t = float(row["t_unix"]) - origin
            if not batches or t != batches[-1]["t"]:
                if batches and t < batches[-1]["t"]:
                    raise ValueError("IMU receive time moved backwards")
                batches.append({"t": t, "count": 0, "min": row["euler"][:], "max": row["euler"][:],
                                "gyro_min": row["gyro"][:], "gyro_max": row["gyro"][:], "samples": []})
            batch = batches[-1]
            batch["count"] += 1
            batch["min"] = [min(a, b) for a, b in zip(batch["min"], row["euler"])]
            batch["max"] = [max(a, b) for a, b in zip(batch["max"], row["euler"])]
            batch["gyro_min"] = [min(a, b) for a, b in zip(batch["gyro_min"], row["gyro"])]
            batch["gyro_max"] = [max(a, b) for a, b in zip(batch["gyro_max"], row["gyro"])]
            batch["samples"].append({key: row[key] for key in ("tid", "euler", "gyro", "accel", "quaternion")})
            for key in ("tid", "euler", "gyro", "accel", "quaternion"):
                batch[key] = row[key]
    with trajectory.open(encoding="utf-8", newline="") as source:
        poses = [{"t": float(r["t_s"]) - origin, "x": float(r["x"]), "y": float(r["y"]),
                  "z": float(r["z"])} for r in csv.DictReader(source)]
    if not batches or not poses:
        raise ValueError("IMU batches and diagnostic trajectory are required")
    template = Path(__file__).with_name("na70_host_timeline_view.html")
    model_path = ROOT / "tools/imu_live/na70-yesense-approx-mm.obj"
    data = {"origin_unix_s": origin, "frames": frames, "batches": batches, "poses": poses,
            "model": load_model(model_path), "model_sha256": sha256(model_path),
            "sources": {str(p): sha256(p) for p in (pcap, imu, trajectory)},
            "generator_sha256": sha256(Path(__file__)), "template_sha256": sha256(template),
            "points_per_frame": points_per_frame}
    output.mkdir(parents=True, exist_ok=False)
    page = template.read_text(encoding="utf-8").replace("/*__DATA__*/", json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    (output / "index.html").write_text(page, encoding="utf-8")
    summary = {"status": "DIAGNOSTIC_ONLY", "origin_unix_s": origin,
               "frames": len(frames), "last_frame_complete": frames[-1]["complete"],
               "imu_batches": len(batches), "imu_frames": sum(x["count"] for x in batches),
               "imu_first_s": batches[0]["t"], "imu_last_s": batches[-1]["t"],
               "trajectory_poses": len(poses), "trajectory_first_s": poses[0]["t"],
               "trajectory_last_s": poses[-1]["t"], "sources": data["sources"],
               "generator_sha256": data["generator_sha256"], "template_sha256": data["template_sha256"],
               "model_sha256": data["model_sha256"], "page_sha256": sha256(output / "index.html"), "points_per_frame": points_per_frame}
    (output / "manifest.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--session", required=True, type=Path)
    p.add_argument("--trajectory", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--points-per-frame", type=int, default=1200)
    a = p.parse_args()
    print(json.dumps(export(a.session, a.trajectory, a.output, points_per_frame=a.points_per_frame), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
