import numpy as np
import pytest

from backend.temporal_yaw import fit_temporal_yaw, yaw_offset_at


def test_temporal_yaw_fit_recovers_slow_rotation_with_outlier():
    measurements = [
        {"time": float(i), "heading_unwrapped_rad": np.deg2rad(0.08 * i), "heading_rad": 0.0}
        for i in range(20)
    ]
    measurements[8]["heading_unwrapped_rad"] += np.deg2rad(8.0)
    report = fit_temporal_yaw(measurements)
    assert report["valid"]
    assert abs(report["yaw_rate_deg_s"] - 0.08) < 0.01
    assert report["residual_mad_deg"] < 0.1
    assert yaw_offset_at(report, 10.0) == pytest.approx(np.deg2rad(0.8), abs=1e-6)


def test_temporal_yaw_rejects_short_profile():
    report = fit_temporal_yaw([])
    assert not report["valid"]
    assert report["reason"] == "too_few_ground_measurements"
