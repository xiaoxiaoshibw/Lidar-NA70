"""SYNTHETIC input contracts: no SLAM accuracy claims."""
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from na70_core import RayBatch
from na70_core.lio import ClockModel, ImuSamples, extrinsics, prepare_inputs
from test_decoder import make_pcap
from test_thin_wire import timed_payload

ROOT = Path(__file__).resolve().parents[2]


def config():
    cfg = json.loads((ROOT / "tools/lio/preflight_default.yaml").read_text())
    for key in ("lidar_clock", "imu_clock"):
        cfg[key].update(uncertainty_s=0.0001, evidence="SYNTHETIC known clock")
    cfg.update(T_imu_lidar=np.eye(4).tolist(), calibration_evidence="SYNTHETIC known mounting",
               packet_time_uncertainty_s=0.0001, accel_includes_gravity=True,
               gyro_range_rad_s=30.0, accel_range_m_s2=150.0)
    return cfg


def batch(times=(10., 10., 10.01, 10.01)):
    return RayBatch.from_decoded(
        capture_sha256="a" * 64, coordinate_frame="lidar_link", install_direction=1,
        codes=np.array([[100, 0, 0, 8], [0, 0, 0, 0], [100, 0, 0, 9], [100, 0, 0, 10]], dtype=np.uint16),
        points=np.array([[1, 0, 0, 8], [0, 0, 0, 0], [2, 0, 0, 9], [100, 0, 0, 10]], dtype=np.float32),
        frame_indices=np.zeros(4, dtype=np.int64), packet_indices=np.array([30, 30, 31, 31]),
        point_indices=np.array([0, 1, 200, 201]), device_times=np.array(times), capture_times=np.ones(4))


def imu(times=None):
    times = np.arange(9.99, 10.031, .005) if times is None else np.array(times)
    return ImuSamples.from_arrays(times, np.zeros((len(times), 3)),
                                  np.tile([0, 0, 9.80665], (len(times), 1)), frame_id="imu_link")


def test_packet_times_refs_and_range_filter_are_not_renumbered():
    source = batch()
    report, out = prepare_inputs(source, imu(), config())
    assert report["status"] == "INPUT_CONTRACT_PASSED"
    assert report["time_quality"] == "PACKET_APPROXIMATION"
    assert report["deskew_applied"] is False
    assert not any(report["claims"].values())
    np.testing.assert_array_equal(out["source_refs"], [[0, 30, 0], [0, 31, 200]])
    np.testing.assert_array_equal(out["points"], source.points[[0, 2]])
    np.testing.assert_allclose(out["relative_time_s"], [0, .01])
    assert report["invalid_return_count"] == 1
    assert report["outside_range_count"] == 1


def test_unknown_hardware_does_not_pass_and_has_no_fake_imu():
    cfg = json.loads((ROOT / "tools/lio/preflight_default.yaml").read_text())
    report, out = prepare_inputs(batch(), None, cfg)
    assert report["status"] == "BLOCKED"
    assert {"imu_missing", "lidar_clock_unverified", "packet_time_uncertainty_unknown",
            "extrinsic_unknown", "specific_force_not_confirmed"} <= set(report["block_reasons"])
    assert "imu_time_s" not in out


@pytest.mark.parametrize("times,match", [((10., 10., 9., 9.), "rewind"),
                                        ((10., 10., 11., 11.), "gap"),
                                        ((10., 10., np.nan, np.nan), "invalid_device"),
                                        ((10., 10.001, 10.01, 10.01), "packet time")])
def test_bad_lidar_time_is_never_replaced_by_host_time(times, match):
    with pytest.raises(ValueError, match=match):
        prepare_inputs(batch(times), imu(), config())


def test_extrinsics_inverse_has_rotation_and_lever_arm():
    matrix = np.array([[0, -1, 0, .3], [1, 0, 0, -.2], [0, 0, 1, .1], [0, 0, 0, 1.]])
    forward, inverse = extrinsics(matrix)
    np.testing.assert_allclose(inverse @ forward, np.eye(4), atol=1e-12)
    cfg = config()
    cfg["T_imu_lidar"] = matrix.tolist()
    report, _ = prepare_inputs(batch(), imu(), cfg)
    np.testing.assert_array_equal(report["transforms"]["glim_T_lidar_imu"], inverse)
    for bad in (np.diag([-1., 1, 1, 1]), np.eye(3), np.eye(4) * 2):
        with pytest.raises(ValueError):
            extrinsics(bad)


def test_time_budget_bracketing_gap_and_saturation():
    cfg = config()
    cfg["packet_time_uncertainty_s"] = 0.1
    report, _ = prepare_inputs(batch(), imu([10., 10.03]), cfg)
    assert {"time_error_budget_exceeded", "imu_gap"} <= set(report["block_reasons"])
    report, _ = prepare_inputs(batch(), imu([10.005, 10.006]), config())
    assert "imu_does_not_bracket_window" in report["block_reasons"]
    cfg = config()
    cfg["accel_range_m_s2"] = 9.0
    report, _ = prepare_inputs(batch(), imu(), cfg)
    assert "accel_range_m_s2_saturated" in report["block_reasons"]


def test_imu_csv_units_and_time_order(tmp_path):
    path = tmp_path / "imu.csv"
    path.write_text("device_time_s,gx,gy,gz,ax,ay,az\n10,180,0,0,0,0,1\n10.01,0,0,0,0,0,1\n")
    data = ImuSamples.from_csv(path, frame_id="imu_link", gyro_unit="deg/s", accel_unit="g", max_samples=10)
    assert data.gyro_rad_s[0, 0] == pytest.approx(np.pi)
    assert data.accel_m_s2[0, 2] == pytest.approx(9.80665)
    with pytest.raises(ValueError, match="max_samples"):
        ImuSamples.from_csv(path, frame_id="imu_link", gyro_unit="rad/s", accel_unit="g", max_samples=1)
    with pytest.raises(ValueError, match="strictly increase"):
        imu([10, 10])
    with pytest.raises(ValueError, match="strictly increase"):
        imu([10, 9])


def test_clock_origin_rate_and_invalid_mapping():
    model = ClockModel(1_700_000_000., 5., 1.0001)
    np.testing.assert_allclose(model.map([1_700_000_000., 1_700_000_001.]), [5, 6.0001])
    with pytest.raises(ValueError):
        ClockModel(0, 0, -1)


def test_cli_is_reproducible_blocks_missing_imu_and_refuses_overwrite(tmp_path):
    pcap = make_pcap(tmp_path / "SYNTHETIC.pcap", [timed_payload(10 + i * .001) for i in range(201)])
    output = tmp_path / "result"
    command = [sys.executable, str(ROOT / "tools/prepare_lio_session.py"), "--pcap", str(pcap),
               "--end-frame", "1", "--evidence-kind", "SYNTHETIC", "--output", str(output)]
    run = subprocess.run(command, capture_output=True, text=True)
    assert run.returncode == 2, run.stderr
    report = json.loads((output / "report.json").read_text())
    assert "imu_missing" in report["block_reasons"]
    assert report["evidence_kind"] == "SYNTHETIC"
    with np.load(output / "inputs.npz", allow_pickle=False) as arrays:
        assert arrays["raw_source_refs"].shape[1] == 3
    original = (output / "report.json").read_bytes()
    assert subprocess.run(command, capture_output=True).returncode != 0
    assert (output / "report.json").read_bytes() == original


def test_cli_accepts_synthetic_csv_without_claiming_hardware_validation(tmp_path):
    pcap = make_pcap(tmp_path / "SYNTHETIC.pcap", [timed_payload(10 + i * .001) for i in range(201)])
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(json.dumps(config()))
    imu_path = tmp_path / "imu.csv"
    imu_path.write_text("device_time_s,gx,gy,gz,ax,ay,az\n" + "".join(
        f"{t:.6f},0,0,0,0,0,9.80665\n" for t in np.arange(9.99, 10.301, .005)))
    output = tmp_path / "prepared"
    run = subprocess.run([sys.executable, str(ROOT / "tools/prepare_lio_session.py"),
                          "--pcap", str(pcap), "--imu-csv", str(imu_path), "--config", str(cfg_path),
                          "--end-frame", "1", "--evidence-kind", "SYNTHETIC", "--output", str(output)],
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stderr + run.stdout
    report = json.loads((output / "report.json").read_text())
    assert report["status"] == "INPUT_CONTRACT_PASSED"
    assert not any(report["claims"].values())
    with np.load(output / "inputs.npz", allow_pickle=False) as arrays:
        assert arrays["imu_time_s"][0] <= report["reference_time_s"]
        assert arrays["imu_time_s"][-1] >= report["end_time_s"]


def test_cli_rejects_nonfinite_config_and_saves_diagnostic(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text('{"bad": NaN}')
    output = tmp_path / "invalid"
    run = subprocess.run([sys.executable, str(ROOT / "tools/prepare_lio_session.py"),
                          "--pcap", str(tmp_path / "missing.pcap"), "--config", str(cfg),
                          "--end-frame", "1", "--evidence-kind", "SYNTHETIC", "--output", str(output)],
                         capture_output=True, text=True)
    assert run.returncode == 2, run.stderr
    report = json.loads((output / "report.json").read_text())
    assert report["status"] == "BLOCKED" and "nonfinite JSON" in report["block_reasons"][0]
