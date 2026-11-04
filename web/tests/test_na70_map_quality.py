"""Tests for map quality checks and the map evaluation CLI (SYNTHETIC maps)."""
import importlib.util
import json
from pathlib import Path

import numpy as np

from na70_core import map_quality, scene

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("evaluate_na70_map", ROOT / "tools/evaluate_na70_map.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def grid(z=0.0, count=20):
    x, y = np.meshgrid(np.linspace(0, 1, count), np.linspace(0, 1, count))
    return np.column_stack([x.ravel(), y.ravel(), np.full(x.size, z)])


def save_map(path, points):
    result = scene.MapResult(
        points=np.asarray(points, dtype=np.float32),
        intensity=np.zeros(len(points), dtype=np.uint8),
        observations=np.ones(len(points), dtype=np.uint32),
        source_frame=np.zeros(len(points), dtype=np.int32),
        stats={})
    result.save_ply(path)
    return path


def test_ply_roundtrip_and_map_consistency(tmp_path):
    map_path = save_map(tmp_path / "map.ply", grid(0.0))
    reference = save_map(tmp_path / "ref.ply", grid(0.1))
    loaded = map_quality.load_ply(map_path)
    assert len(loaded) == 400
    assert loaded.dtype.names == ("x", "y", "z", "intensity", "observations", "source_frame")
    result = map_quality.map_consistency(grid(0.0), grid(0.1), threshold_m=0.15)
    assert result["status"] == "EVALUATED"
    assert abs(result["forward"]["p50_m"] - 0.1) < 1e-9
    assert result["forward"]["within_fraction"] == 1.0
    assert result["claims"]["absolute_accuracy_validated"] is False


def test_plane_and_control_point_evaluation(tmp_path):
    points = grid(0.02)
    planes = [{"name": "ground", "point": [0, 0, 0], "normal": [0, 0, 1]}]
    result = map_quality.plane_evaluation(points, planes, threshold_m=0.05)
    assert result["planes"][0]["p50_m"] == np.float64(0.02) or abs(result["planes"][0]["p50_m"] - 0.02) < 1e-9
    control = points[::80]
    result = map_quality.control_point_evaluation(points, control, threshold_m=0.01)
    assert result["summary"]["max_m"] < 1e-9


def test_plane_bounds_exclude_other_surfaces():
    points = np.vstack([grid(0.0), grid(1.0)])
    planes = [{"name": "ground", "point": [0, 0, 0], "normal": [0, 0, 1],
               "bounds": {"minimum": [-1, -1, -0.1], "maximum": [2, 2, 0.1]}}]
    result = map_quality.plane_evaluation(points, planes, threshold_m=0.05)
    plane = result["planes"][0]
    assert plane["selected_points"] == 400
    assert plane["p95_m"] < 1e-9
    assert plane["within_fraction"] == 1.0


def test_cli_writes_all_sections(tmp_path):
    map_path = save_map(tmp_path / "map.ply", grid(0.0))
    reference = save_map(tmp_path / "ref.ply", grid(0.05))
    planes = tmp_path / "planes.json"
    planes.write_text(json.dumps([{"name": "ground", "point": [0, 0, 0],
                                   "normal": [0, 0, 1]}]), encoding="utf-8")
    control = tmp_path / "control.csv"
    control.write_text("x,y,z\n0,0,0\n1,1,0\n", encoding="utf-8")
    output = tmp_path / "evaluation.json"
    code = module.main(["--map", str(map_path), "--reference", str(reference),
                        "--planes", str(planes), "--control-points", str(control),
                        "--threshold-m", "0.1", "--output", str(output)])
    assert code == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "EVALUATED"
    assert set(report["results"]) == {"map_consistency", "planes", "control_points"}
    assert report["claims"]["registration_applied"] is False


def test_invalid_map_is_rejected(tmp_path):
    bad = tmp_path / "bad.ply"
    bad.write_text("not a ply", encoding="utf-8")
    assert module.main(["--map", str(bad), "--reference", str(bad),
                        "--output", str(tmp_path / "out.json")]) == 2