"""SYNTHETIC cross-capture alignment and rigidity validation."""
import numpy as np

from backend.cross_capture import (
    align_static_submaps, angular_coverage_deg, rotate_xy, validate_temporal_rigidity,
)


def submap():
    x, y = np.meshgrid(np.linspace(-10, 10, 35), np.linspace(-8, 8, 31))
    ground = np.column_stack((x.ravel(), y.ravel(), np.zeros(x.size)))
    structures = []
    for cx, cy, height in [(-7, -3, 2.0), (4, -6, 3.0), (8, 5, 1.5), (-2, 7, 3.5)]:
        z = np.linspace(0.6, height + 0.6, 30)
        structures.append(np.column_stack((np.full(len(z), cx), np.full(len(z), cy), z)))
    wall_y, wall_z = np.meshgrid(np.linspace(-5, 1, 25), np.linspace(0.6, 3.6, 15))
    structures.append(np.column_stack((np.full(wall_y.size, 6.0), wall_y.ravel(), wall_z.ravel())))
    wall_x, wall_z = np.meshgrid(np.linspace(-8, 2, 35), np.linspace(0.6, 2.8, 12))
    structures.append(np.column_stack((wall_x.ravel(), np.full(wall_x.size, 5.5), wall_z.ravel())))
    return np.concatenate((ground, *structures))


def inverse_transform(points, yaw, translation):
    result = points.copy()
    result[:, :2] = rotate_xy(result[:, :2] - np.asarray(translation), -yaw)
    return result


def test_alignment_recovers_rotation_and_translation_on_held_out_points():
    reference = submap()
    source = inverse_transform(reference, 17.0, [0.22, -0.11])
    report = align_static_submaps(source, reference, yaw_bounds=(-30, 30), yaw_step=1.0)
    # A 0.25 m top-view voxel grid supports coarse static-submap validation,
    # not the old script's unjustified 0.02 degree precision claim.
    assert abs(report["yaw_deg"] - 17.0) < 2.0
    np.testing.assert_allclose(report["translation_xy_m"], [0.22, -0.11], atol=0.15)
    assert report["heldout"]["overlap"]["0.3"] > 0.95
    assert report["full_symmetric"]["symmetric_overlap"]["0.3"] > 0.95
    assert report["peak_margin_m"] > 0.05


def test_temporal_pose_change_rejects_single_rigid_submap_assumption():
    changing = [
        {"yaw_deg": -2.0, "translation_xy_m": [0.0, 0.0]},
        {"yaw_deg": -12.0, "translation_xy_m": [0.05, 0.0]},
        {"yaw_deg": -23.0, "translation_xy_m": [0.08, 0.0]},
    ]
    report = validate_temporal_rigidity(changing)
    assert not report["rigid"]
    assert report["yaw_spread_deg"] == 21.0

    stable = [
        {"yaw_deg": 5.0, "translation_xy_m": [0.02, -0.01]},
        {"yaw_deg": 5.2, "translation_xy_m": [0.01, -0.02]},
    ]
    assert validate_temporal_rigidity(stable)["rigid"]


def test_angular_coverage_handles_wraparound():
    angles = np.radians([350, 355, 0, 5, 10])
    points = np.column_stack((np.cos(angles), np.sin(angles), np.zeros(len(angles))))
    assert angular_coverage_deg([points]) == 20.0
