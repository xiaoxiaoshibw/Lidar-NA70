"""CLI test for tools/build_na70_scene.py with a SYNTHETIC decoder stub."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[2]


def load_tool():
    spec = importlib.util.spec_from_file_location("build_na70_scene", ROOT / "tools/build_na70_scene.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_scene_from_poses(tmp_path, monkeypatch):
    tool = load_tool()
    poses = tmp_path / "poses.csv"
    poses.write_text("t_s,tx,ty,tz,qx,qy,qz,qw\n"
                     "0.0,0,0,0,0,0,0,1\n"
                     "0.1,0.5,0,0,0,0,0,1\n"
                     "0.2,1.0,0,0,0,0,0,1\n", encoding="utf-8")
    pcap = tmp_path / "dummy.pcap"
    pcap.write_bytes(b"")
    monkeypatch.setattr(tool.decoder, "scan_pcap",
                        lambda path: SimpleNamespace(frames=[
                            {"packet_capture_times": [0.0]},
                            {"packet_capture_times": [0.1]}]))
    points = np.tile(np.array([[1.0, 0.0, 0.0, 7.0]], dtype="<f4"), (200, 1))
    monkeypatch.setattr(tool.decoder, "decode_frame",
                        lambda path, frame, install_direction=1, source=None: points.copy())
    output = tmp_path / "scene"
    code = tool.main(["--pcap", str(pcap), "--poses", str(poses), "--pose-frame", "lidar",
                      "--voxel-size", "0.05", "--output", str(output)])
    assert code == 0
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "BUILT"
    assert report["map"]["voxels"] == 2
    assert report["map"]["bounds"]["minimum"][0] == 1.0
    assert report["map"]["bounds"]["maximum"][0] == 1.5
    assert (output / "map.ply").stat().st_size > 0
    trajectory = (output / "trajectory.csv").read_text(encoding="utf-8").splitlines()
    assert trajectory[0] == "t_s,tx,ty,tz,qx,qy,qz,qw"
    assert len(trajectory) == 4
    assert trajectory[1].startswith("0.000000,0.000000000,0.000000000,0.000000000,0.000000000,0.000000000,0.000000000,1.000000000")


def test_imu_poses_require_calibration(tmp_path):
    tool = load_tool()
    poses = tmp_path / "poses.csv"
    poses.write_text("0,0,0,0,0,0,0,1\n0.1,0,0,0,0,0,0,1\n", encoding="utf-8")
    pcap = tmp_path / "dummy.pcap"
    pcap.write_bytes(b"")
    code = tool.main(["--pcap", str(pcap), "--poses", str(poses), "--pose-frame", "imu",
                      "--output", str(tmp_path / "scene")])
    assert code == 2