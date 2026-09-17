"""SYNTHETIC localizability and constrained SE(2) regression."""
import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from backend.mapping import estimate_normals, invert, transform
from backend.registration_se2 import (
    point_to_plane_observability, register_se2, scene_structure_statistics,
    select_se2_structures,
)


def ground(size=13):
    x, y = np.meshgrid(np.linspace(-4, 4, size), np.linspace(-4, 4, size))
    return np.column_stack((x.ravel(), y.ravel(), np.zeros(x.size)))


def structured_cloud():
    parts = [ground(17)]
    z = np.linspace(0, 3, 25)
    for x, y in [(-3, -2), (2.5, -1), (-1, 3), (3, 2.5)]:
        angles = np.linspace(0, 2 * np.pi, 16, endpoint=False)
        aa, zz = np.meshgrid(angles, z)
        parts.append(np.column_stack((x + 0.18 * np.cos(aa.ravel()),
                                      y + 0.18 * np.sin(aa.ravel()), zz.ravel())))
    # Two non-parallel vertical surfaces make x/y/yaw jointly observable.
    a, b = np.meshgrid(np.linspace(-4, 4, 17), np.linspace(0, 3, 11))
    parts.append(np.column_stack((a.ravel(), np.full(a.size, -4.0), b.ravel())))
    parts.append(np.column_stack((np.full(a.size, 4.0), a.ravel(), b.ravel())))
    return np.concatenate(parts)


def pose(x=0.12, y=-0.08, yaw_deg=2.0):
    result = np.eye(4)
    result[:3, :3] = Rotation.from_euler("z", yaw_deg, degrees=True).as_matrix()
    result[:2, 3] = [x, y]
    return result


def cfg(**kwargs):
    result = dict(
        min_points=40, normal_neighbors=12, gicp_neighbors=12,
        correspondence_distance=0.8, correspondence_trim=0.95,
        max_iterations=35, convergence_m=1e-5,
        max_iteration_rotation_deg=4.0, max_iteration_translation=0.3,
        se2_observability_ratio=0.005, se2_huber_delta=0.20,
        gicp_huber_delta=1.5, gicp_regularization=0.01,
        ndt_resolutions=[1.2, 0.6], ndt_min_points=3,
        ndt_regularization=0.04, ndt_huber_delta=2.0,
        min_overlap=0.70, max_rmse=0.30,
        max_translation_step=0.5, max_rotation_step=8.0,
    )
    result.update(kwargs)
    return result


def test_six_dof_observability_identifies_ground_plane_nullspace():
    points = ground()
    normals = np.tile([0.0, 0.0, 1.0], (len(points), 1))
    report = point_to_plane_observability(points, normals, ratio=0.01)
    assert report["rank"] == 3
    assert report["dof_scores"]["z"] > 0.9
    assert report["dof_scores"]["roll"] > 0.1
    assert report["dof_scores"]["pitch"] > 0.1
    assert report["dof_scores"]["x"] == pytest.approx(0)
    assert report["dof_scores"]["y"] == pytest.approx(0)
    assert report["dof_scores"]["yaw"] == pytest.approx(0)


def test_empty_observability_is_explicit_zero_rank():
    report = point_to_plane_observability(np.empty((0, 3)), np.empty((0, 3)))
    assert report["rank"] == 0
    assert report["rotation_scale_m"] is None
    assert all(score == 0 for score in report["dof_scores"].values())


def test_geometry_statistics_separate_ground_vertical_and_thin_candidates():
    report = scene_structure_statistics(structured_cloud())
    assert report["ground_like"] > 100
    assert report["vertical_like"] > 100
    assert report["thin_structure_like"] > 100
    assert report["semantics"] == "geometry_candidates_only"

    selected, mask = select_se2_structures(structured_cloud())
    assert len(selected) == int(mask.sum())
    assert 100 < len(selected) < len(structured_cloud())


def test_ground_only_freezes_unobservable_se2_instead_of_inventing_pose():
    points = ground(17)
    estimate, quality = register_se2(points, points, np.eye(4), cfg(), "point_to_plane")
    assert estimate is None
    assert quality["state"] == "degenerate_geometry"
    assert quality["reason"] == "se2_structure_insufficient"
    assert set(quality["frozen_directions"]) == {"yaw", "x", "y"}


@pytest.mark.parametrize("method,tolerance", [
    ("point_to_plane", 0.045), ("gicp", 0.06), ("ndt", 0.16),
])
def test_se2_candidates_recover_translation_rotation_and_combined_motion(method, tolerance):
    target = structured_cloud()
    truth = pose()
    source = transform(target, invert(truth))
    estimate, quality = register_se2(source, target, np.eye(4), cfg(), method)
    assert estimate is not None, quality
    assert quality["state"] == "accepted"
    if method == "gicp":
        assert quality["resolutions_m"] == [0.8, 0.4, 0.25]
    if method == "ndt":
        assert quality["resolutions_m"] == [1.2, 0.6]
    assert np.linalg.norm(estimate[:2, 3] - truth[:2, 3]) < tolerance
    yaw = Rotation.from_matrix(estimate[:3, :3]).as_euler("zyx", degrees=True)[0]
    assert abs(yaw - 2.0) < (1.0 if method == "ndt" else 0.4)
    np.testing.assert_allclose(estimate[2, :], [0, 0, 1, 0], atol=1e-10)


def test_bad_correspondences_are_rejected():
    source = structured_cloud()
    target = source + np.array([20.0, 0.0, 0.0])
    estimate, quality = register_se2(source, target, np.eye(4), cfg(), "gicp")
    assert estimate is None
    assert quality["state"] == "insufficient_points"
    assert quality["reason"] == "no_correspondence"
