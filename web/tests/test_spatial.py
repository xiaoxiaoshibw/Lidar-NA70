"""SYNTHETIC equivalence tests for the shared weighted DBSCAN.

The vectorised implementation must return exactly the labels of the original
seed-by-seed BFS, including cluster numbering and noise assignment.  Every case
here uses generated points, never NA70 captures, so these are logic regressions
and must not be quoted as perception performance evidence.
"""

import numpy as np
from scipy.spatial import cKDTree

from backend.spatial import weighted_dbscan


def reference_dbscan(points, weights, eps, min_points):
    """Seed-by-seed BFS DBSCAN, retained as the equivalence reference."""
    if len(points) == 0:
        return np.empty(0, dtype=np.int32)
    tree = cKDTree(points)
    labels = np.full(len(points), -2, dtype=np.int32)
    cluster_id = 0
    for seed in range(len(points)):
        if labels[seed] != -2:
            continue
        neighbours = tree.query_ball_point(points[seed], eps)
        if int(weights[neighbours].sum()) < min_points:
            labels[seed] = -1
            continue
        labels[seed] = cluster_id
        queue = list(neighbours)
        queued = set(queue)
        cursor = 0
        while cursor < len(queue):
            current = queue[cursor]
            cursor += 1
            if labels[current] == -1:
                labels[current] = cluster_id
            if labels[current] != -2:
                continue
            labels[current] = cluster_id
            nearby = tree.query_ball_point(points[current], eps)
            if int(weights[nearby].sum()) >= min_points:
                for neighbour in nearby:
                    if neighbour not in queued:
                        queue.append(neighbour)
                        queued.add(neighbour)
        cluster_id += 1
    return labels


def test_matches_reference_over_random_point_sets():
    rng = np.random.default_rng(20260910)
    for _ in range(300):
        count = int(rng.integers(0, 70))
        points = rng.normal(scale=1.0, size=(count, 3))
        weights = rng.integers(1, 5, size=count).astype(np.int64)
        eps = float(rng.choice([0.3, 0.6, 1.2]))
        min_points = int(rng.integers(1, 8))
        assert np.array_equal(reference_dbscan(points, weights, eps, min_points),
                              weighted_dbscan(points, weights, eps, min_points))


def test_empty_input_returns_no_labels():
    assert weighted_dbscan(np.empty((0, 3)), np.empty(0, dtype=np.int64), 0.5, 3).size == 0


def test_isolated_below_weight_points_are_noise():
    points = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]])
    weights = np.array([1, 1], dtype=np.int64)
    assert np.array_equal(weighted_dbscan(points, weights, 0.5, 3), np.array([-1, -1], np.int32))


def test_isolated_core_points_form_separate_clusters_in_index_order():
    points = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]])
    weights = np.array([5, 7], dtype=np.int64)
    assert np.array_equal(weighted_dbscan(points, weights, 0.5, 3), np.array([0, 1], np.int32))
