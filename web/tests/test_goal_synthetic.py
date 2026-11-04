"""SYNTHETIC generator and truth-aware evaluator contract tests."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from backend.goal_detection.synthetic import SCENARIOS, generate_sequence, scenario_manifest
from run_goal_synthetic_suite import evaluate_frames


def test_generator_is_seed_reproducible_and_truth_is_separate():
    first = generate_sequence("translation_yaw", 7, frames=12)
    second = generate_sequence("translation_yaw", 7, frames=12)
    changed = generate_sequence("translation_yaw", 8, frames=12)
    assert np.array_equal(first[3].points, second[3].points)
    assert first[3].truth == second[3].truth
    assert not np.array_equal(first[3].points, changed[3].points)
    assert not hasattr(first[3].points, "truth")


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_every_scenario_has_valid_reconstructable_truth(scenario):
    sequence = generate_sequence(scenario, 3, frames=40)
    assert len(sequence) == 40
    assert [item.truth.frame_index for item in sequence] == list(range(40))
    for item in sequence:
        assert item.points.ndim == 2 and item.points.shape[1] == 4
        if item.truth.visible:
            assert item.truth.center is not None and len(item.truth.center) == 3


def test_disappear_reenter_contains_true_absence_interval():
    sequence = generate_sequence("disappear_reenter", 1, frames=100)
    visibility = [item.truth.visible for item in sequence]
    assert all(visibility[:35])
    assert not any(visibility[35:60])
    assert all(visibility[60:])


def test_evaluator_rejects_stable_wrong_target_and_id_switches():
    truths = [{"visible": True, "center": [75.0, 0.0, 1.2],
               "size": [0.6, 5.4, 2.4]} for _ in range(5)]
    rows = [{"selected_measurement": {"center": [68.0, 11.0, 2.0]},
             "goal": {"id": 1}} for _ in range(5)]
    result = evaluate_frames(rows, truths)
    assert result["hits"] == 0 and result["recall"] == 0.0
    assert result["false_outputs"] == 5

    rows = [{"selected_measurement": {"center": [75.1, 0.0, 1.2],
                                       "size": [0.6, 5.7, 2.6]},
             "goal": {"id": goal_id}} for goal_id in (1, 1, 2, 2, 3)]
    result = evaluate_frames(rows, truths)
    assert result["hits"] == 5 and result["id_switches"] == 2
    assert result["width_error_p95_m"] == pytest.approx(0.3)
    assert result["height_error_p95_m"] == pytest.approx(0.2)
    assert result["longest_hit_streak"] == 5


def test_manifest_freezes_seed_ranges_and_limits():
    manifest = scenario_manifest()
    assert manifest["label"] == "SYNTHETIC"
    assert manifest["development_seeds"] == [0, 19]
    assert manifest["validation_seeds"] == [1000, 1049]
    assert manifest["default_frames"] == 200
    assert manifest["truth_not_provided_to_pipeline"] is True
