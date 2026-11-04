"""Integration check: the scene builder must drive the real NA70 decoder.

This uses a real PCAP only as an input-shape test (identity trajectory, two
frames).  It is not a reconstruction result and makes no accuracy claim.
"""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PCAP = ROOT / "radar_capture_data/na70_sessions/a2-browser-155552/lidar.pcap"

pytestmark = pytest.mark.skipif(not PCAP.is_file(), reason="real capture not present")


def load_tool():
    spec = importlib.util.spec_from_file_location("build_na70_scene", ROOT / "tools/build_na70_scene.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scene_builder_runs_on_real_pcap(tmp_path):
    from backend import decoder

    tool = load_tool()
    scan = decoder.scan_pcap(PCAP)
    times = [time for frame in scan.frames[:2] for time in frame["packet_capture_times"]]
    poses = tmp_path / "identity.csv"
    poses.write_text(
        "t_s,tx,ty,tz,qx,qy,qz,qw\n"
        f"{min(times):.6f},0,0,0,0,0,0,1\n"
        f"{max(times) + 0.01:.6f},0,0,0,0,0,0,1\n", encoding="utf-8")
    output = tmp_path / "scene"
    code = tool.main(["--pcap", str(PCAP), "--poses", str(poses), "--pose-frame", "lidar",
                      "--max-frames", "2", "--voxel-size", "0.2", "--output", str(output)])
    assert code == 0
    report = __import__("json").loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "BUILT"
    assert report["build"]["frames_decoded"] == 2
    assert report["map"]["points"] > 0
    assert report["map"]["dropped"]["outside"] == 0
    assert report["claims"]["map_accuracy_validated"] is False