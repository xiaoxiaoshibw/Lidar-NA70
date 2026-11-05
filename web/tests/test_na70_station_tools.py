"""End-to-end tests for the station-merge and replay-compare CLI tools
using SYNTHETIC inputs."""
import importlib.util
import json
import math
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"tools/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rotation_about_z(angle_deg):
    angle = math.radians(angle_deg)
    cos, sin = math.cos(angle), math.sin(angle)
    return np.array([[cos, -sin, 0.0], [sin, cos, 0.0], [0.0, 0.0, 1.0]])


def write_ascii_ply(path, xyz, observations=None):
    observations = np.ones(len(xyz), dtype=int) if observations is None else observations
    with path.open("w", encoding="ascii") as stream:
        stream.write("ply\nformat ascii 1.0\nelement vertex %d\n"
                     "property float x\nproperty float y\nproperty float z\n"
                     "property uint observations\nend_header\n" % len(xyz))
        for (x, y, z), n in zip(xyz, observations):
            stream.write("%.4f %.4f %.4f %d\n" % (x, y, z, n))


def mini_room(rng, count=9000):
    """Three walls at 6 m (small, fast), mild noise."""
    specs = [(0, 6.0, (-8.0, 8.0), (-6.0, 6.0)),
             (1, 6.0, (-8.0, 8.0), (-6.0, 6.0)),
             (2, 6.0, (-8.0, 8.0), (-8.0, 8.0))]
    walls = []
    for fixed, value, urange, vrange in specs:
        n = count // 3
        free = [axis for axis in range(3) if axis != fixed]
        points = np.empty((n, 3))
        points[:, fixed] = value
        points[:, free[0]] = rng.uniform(*urange, n)
        points[:, free[1]] = rng.uniform(*vrange, n)
        walls.append(points)
    return np.concatenate(walls, axis=0) + rng.normal(0, 0.004, (count, 3))


def test_compare_tool_recomputes_exact_statistics(tmp_path):
    tool = load_tool("compare_lios_replays")
    # poses at t=0,1,2 with unit steps along x: hand-checkable metrics
    rows = [(0.0, 0, 0, 0), (1.0, 1, 0, 0), (2.0, 1, 1, 0), (2.5, 1, 1, 2)]
    path = tmp_path / "trajectory.csv"
    path.write_text("t_s,x,y,z,qx,qy,qz,qw\n"
                    + "".join(f"{t},{x},{y},{z},0,0,0,1\n" for t, x, y, z in rows),
                    encoding="ascii")
    stats = tool.trajectory_statistics(tool.load_trajectory(path))
    assert stats["pose_rows"] == 4
    assert stats["time_span_s"] == pytest.approx(2.5)
    assert stats["path_length_m"] == pytest.approx(1 + 1 + 2)
    assert stats["max_gap_s"] == pytest.approx(1.0)
    assert stats["median_gap_s"] == pytest.approx(1.0)
    assert stats["max_radius_m"] == pytest.approx(math.sqrt(1 + 1 + 4))
    assert stats["max_step_m"] == pytest.approx(2.0)


def test_compare_tool_rejects_bad_csv(tmp_path):
    tool = load_tool("compare_lios_replays")
    bad_header = tmp_path / "bad_header.csv"
    bad_header.write_text("t,x,y,z,qx,qy,qz,qw\n0,0,0,0,0,0,0,1\n",
                          encoding="ascii")
    with pytest.raises(ValueError, match="header"):
        tool.load_trajectory(bad_header)
    rewind = tmp_path / "rewind.csv"
    rewind.write_text("t_s,x,y,z,qx,qy,qz,qw\n2,0,0,0,0,0,0,1\n"
                      "1,0,0,0,0,0,0,1\n", encoding="ascii")
    with pytest.raises(ValueError, match="non-decreasing"):
        tool.load_trajectory(rewind)


def test_compare_tool_end_to_end(tmp_path):
    tool = load_tool("compare_lios_replays")
    rng = np.random.default_rng(5)
    times = np.arange(0.0, 4.0, 0.1)
    walk = np.column_stack([times, 2 * times, np.zeros_like(times),
                            0.1 * times])
    walk += rng.normal(0, 0.01, walk.shape)
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    header = "t_s,x,y,z,qx,qy,qz,qw\n"
    first.write_text(header + "".join(
        f"{t:.3f},{x:.4f},{y:.4f},{z:.4f},0,0,0,1\n"
        for t, x, y, z in walk), encoding="ascii")
    second.write_text(header + "".join(
        f"{t:.3f},{x:.4f},{y + 5:.4f},{z:.4f},0,0,0,1\n"
        for t, x, y, z in walk), encoding="ascii")
    out = tmp_path / "out"
    assert tool.main(["--run", f"a={first}", "--run", f"b={second}",
                      "--out-dir", str(out), "--no-figure"]) == 0
    report = json.loads((out / "replay-comparison.json").read_text(encoding="utf8"))
    assert report["runs"]["a"]["pose_rows"] == report["runs"]["b"]["pose_rows"]
    assert report["runs"]["a"]["path_length_m"] == pytest.approx(
        report["runs"]["b"]["path_length_m"], abs=0.05)
    assert "不共坐标" in report["boundary"]


def test_merge_tool_accepts_synthetic_pair(tmp_path):
    tool = load_tool("merge_na70_station_clouds")
    rng = np.random.default_rng(8)
    scene = mini_room(rng)
    rotation = rotation_about_z(15.0)
    translation = np.array([0.5, -0.3, 0.1])
    moved = scene @ rotation.T + translation
    write_ascii_ply(tmp_path / "s0.ply", scene)
    write_ascii_ply(tmp_path / "s1.ply", moved)
    out = tmp_path / "out"
    code = tool.main(["--station", f"s0={tmp_path / 's0.ply'}",
                      "--station", f"s1={tmp_path / 's1.ply'}",
                      "--out-dir", str(out),
                      "--initial-yaw-deg", "15"])
    assert code == 0
    report = json.loads((out / "merge-report.json").read_text(encoding="utf8"))
    assert report["accepted"] == ["s0", "s1"]
    assert report["refusals"] == []
    reg = report["registrations"]["s1"]
    fitted = np.asarray(reg["rotation"])
    expected = rotation.T  # station -> global is the inverse of world -> station
    angle_error = math.degrees(math.acos(
        np.clip((np.trace(fitted @ expected.T) - 1) / 2, -1, 1)))
    translation_error = float(np.linalg.norm(
        np.asarray(reg["translation"]) + rotation.T @ translation))
    assert angle_error < 0.5, angle_error
    assert translation_error < 0.05, translation_error
    merged_lines = (out / "merged.ply").read_text(encoding="ascii").splitlines()
    assert any(line.startswith("comment stations: s0,s1") for line in merged_lines)


def test_merge_tool_refuses_disjoint_station(tmp_path):
    tool = load_tool("merge_na70_station_clouds")
    rng = np.random.default_rng(9)
    scene = mini_room(rng)
    write_ascii_ply(tmp_path / "s0.ply", scene)
    write_ascii_ply(tmp_path / "far.ply", scene + np.array([80.0, 0, 0]))
    out = tmp_path / "out"
    code = tool.main(["--station", f"s0={tmp_path / 's0.ply'}",
                      "--station", f"far={tmp_path / 'far.ply'}",
                      "--out-dir", str(out)])
    assert code == 2
    report = json.loads((out / "merge-report.json").read_text(encoding="utf8"))
    assert report["accepted"] == ["s0"]
    assert [item["label"] for item in report["refusals"]] == ["far"]
    assert "fitness" in report["refusals"][0]["reason"]


def test_merge_tool_requires_two_stations(tmp_path):
    tool = load_tool("merge_na70_station_clouds")
    rng = np.random.default_rng(10)
    write_ascii_ply(tmp_path / "only.ply", mini_room(rng))
    with pytest.raises(SystemExit):
        tool.main(["--station", f"only={tmp_path / 'only.ply'}",
                   "--out-dir", str(tmp_path / "out")])
