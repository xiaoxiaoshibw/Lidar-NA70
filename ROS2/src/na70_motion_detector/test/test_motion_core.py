import unittest

import numpy as np

from na70_motion_detector.motion_core import MotionDetectorConfig, VoxelMotionDetector


def make_background() -> np.ndarray:
    x, y = np.meshgrid(np.linspace(-3.0, 3.0, 25), np.linspace(-3.0, 3.0, 25))
    xyz = np.column_stack([x.ravel(), y.ravel(), np.zeros(x.size)])
    intensity = np.full((len(xyz), 1), 20.0)
    return np.hstack([xyz, intensity]).astype(np.float32)


def make_moving_object() -> np.ndarray:
    rng = np.random.default_rng(7)
    xyz = rng.normal(loc=(5.0, 0.0, 1.0), scale=(0.12, 0.12, 0.35), size=(60, 3))
    intensity = np.full((len(xyz), 1), 150.0)
    return np.hstack([xyz, intensity]).astype(np.float32)


def make_detector() -> VoxelMotionDetector:
    return VoxelMotionDetector(
        MotionDetectorConfig(
            voxel_size=0.15,
            warmup_frames=5,
            background_min_hits=3,
            background_margin_voxels=1,
            min_moving_points=30,
            min_moving_ratio=0.01,
            confirmation_frames=2,
            clear_frames=2,
        )
    )


class MotionCoreTest(unittest.TestCase):
    def test_requires_empty_scene_warmup_and_temporal_confirmation(self):
        detector = make_detector()
        background = make_background()

        for frame in range(4):
            result = detector.process(background)
            self.assertFalse(result.ready)
            self.assertEqual(result.warmup_frames_seen, frame + 1)
        warmup_result = detector.process(background)
        self.assertTrue(warmup_result.ready)
        self.assertFalse(warmup_result.has_motion)
        self.assertEqual(warmup_result.warmup_frames_seen, 5)

        clear_result = detector.process(background)
        self.assertEqual(clear_result.moving_count, 0)
        self.assertFalse(clear_result.has_motion)

        changed = np.vstack([background, make_moving_object()])
        first = detector.process(changed)
        second = detector.process(changed)
        self.assertGreaterEqual(first.moving_count, 30)
        self.assertFalse(first.has_motion)
        self.assertTrue(second.has_motion)

        self.assertTrue(detector.process(background).has_motion)
        self.assertFalse(detector.process(background).has_motion)

    def test_noise_below_threshold_does_not_trigger(self):
        detector = make_detector()
        background = make_background()
        for _ in range(5):
            detector.process(background)

        few_new_points = make_moving_object()[:5]
        changed = np.vstack([background, few_new_points])
        for _ in range(4):
            result = detector.process(changed)
            self.assertFalse(result.has_motion)

    def test_reset_requires_new_warmup(self):
        detector = make_detector()
        background = make_background()
        for _ in range(5):
            detector.process(background)
        self.assertTrue(detector.ready)

        detector.reset()
        self.assertFalse(detector.ready)
        self.assertFalse(detector.process(background).has_motion)

    def test_rejects_invalid_shape(self):
        detector = make_detector()
        with self.assertRaises(ValueError):
            detector.process(np.zeros((10, 2), dtype=np.float32))


if __name__ == "__main__":
    unittest.main()
