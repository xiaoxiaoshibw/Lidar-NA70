"""Shared spatial primitives for the NA70 offline motion pipeline.

The voxel-weighted DBSCAN lives here because both the legacy per-window
foreground clustering (:mod:`backend.motion`) and the four-dimensional
spatio-temporal band detector (:mod:`backend.bands`) cluster *unique occupied
voxels* weighted by the raw point count inside each voxel.  Keeping one
implementation guarantees the two paths cannot silently drift apart.
"""

from __future__ import annotations

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree


def weighted_dbscan(points: np.ndarray, weights: np.ndarray, eps: float, min_points: int) -> np.ndarray:
    """DBSCAN over occupied voxels, using raw-point counts as sample weights.

    A voxel is a core point when the *sum of raw points* inside its eps
    neighbourhood reaches ``min_points``, so sparse far-field voxels holding a
    single return never seed a cluster while a dense near-field target does.

    One ``query_pairs`` pass plus a connected-components solve over the core
    points replaces the per-seed neighbourhood scan.  The labels are identical
    to a seed-by-seed BFS: a cluster is exactly one connected group of core
    points, a cluster's id follows its lowest-indexed core point, and a non-core
    point joins the cluster of its lowest-indexed core neighbour (which is the
    cluster that reaches it first).
    """
    count = len(points)
    if count == 0:
        return np.empty(0, dtype=np.int32)
    labels = np.full(count, -1, dtype=np.int32)

    pairs = cKDTree(points).query_pairs(eps, output_type="ndarray")
    weight_sum = weights.astype(np.int64).copy()
    if len(pairs):
        row = np.concatenate([pairs[:, 0], pairs[:, 1]])
        column = np.concatenate([pairs[:, 1], pairs[:, 0]])
        weight_sum += np.bincount(row, weights=weights[column].astype(np.float64),
                                  minlength=count).astype(np.int64)
    core = weight_sum >= min_points
    core_index = np.flatnonzero(core)
    if len(core_index) == 0:
        return labels

    if len(pairs) == 0:
        core_pairs = np.empty((0, 2), dtype=np.int64)
    else:
        core_pairs = pairs[core[pairs[:, 0]] & core[pairs[:, 1]]]
    if len(core_pairs) == 0:
        labels[core_index] = np.arange(len(core_index), dtype=np.int32)
    else:
        left = np.searchsorted(core_index, core_pairs[:, 0])
        right = np.searchsorted(core_index, core_pairs[:, 1])
        graph = coo_matrix((np.ones(len(core_pairs), dtype=np.int8), (left, right)),
                           shape=(len(core_index), len(core_index))).tocsr()
        components = connected_components(graph, directed=False)[1]
        # Renumber so a cluster's id is the rank of its lowest core index.
        order = np.argsort(components, kind="stable")
        sorted_components = components[order]
        starts = np.r_[0, np.flatnonzero(np.diff(sorted_components)) + 1]
        first_core_index = core_index[order][starts]
        rank = np.empty(len(first_core_index), dtype=np.int32)
        rank[np.argsort(first_core_index)] = np.arange(len(first_core_index), dtype=np.int32)
        labels[core_index] = rank[components]

    if len(pairs):
        left_core = core[pairs[:, 0]]
        right_core = core[pairs[:, 1]]
        only_left = ~left_core & right_core
        only_right = left_core & ~right_core
        noise = np.where(only_left, pairs[:, 0], np.where(only_right, pairs[:, 1], -1))
        anchor = np.where(only_left, pairs[:, 1], np.where(only_right, pairs[:, 0], -1))
        keep = noise >= 0
        noise, anchor = noise[keep], anchor[keep]
        if len(noise):
            # A non-core point joins the cluster reached first, i.e. the one whose
            # core neighbour belongs to the lowest-numbered (earliest-seeded)
            # cluster -- not simply the lowest-indexed core neighbour.
            anchor_cluster = labels[anchor]
            order = np.lexsort((anchor_cluster, noise))
            sorted_noise = noise[order]
            sorted_cluster = anchor_cluster[order]
            starts = np.r_[0, np.flatnonzero(np.diff(sorted_noise)) + 1]
            labels[sorted_noise[starts]] = sorted_cluster[starts]
    return labels
