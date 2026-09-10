import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import numpy as np

from na70_motion_detector.offline_demo import main


class OfflineDemoTest(unittest.TestCase):
    def test_synthetic_smoke_test(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main([]), 0)
        self.assertIn("has_motion=1", output.getvalue())

    def test_npy_input(self):
        background = np.zeros((12, 3), dtype=np.float32)
        background[:, 0] = np.arange(12, dtype=np.float32) + 1.0
        moving = np.ones((35, 3), dtype=np.float32)
        moving[:, 0] = 30.0
        padded_background = np.full((47, 3), np.nan, dtype=np.float32)
        padded_background[: len(background)] = background
        changed = np.vstack([background, moving])
        frames = np.stack([padded_background, padded_background, padded_background,
                           changed, changed])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frames.npy"
            np.save(path, frames)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(
                    main(
                        [
                            str(path),
                            "--warmup-frames",
                            "3",
                            "--min-moving-points",
                            "30",
                            "--min-moving-ratio",
                            "0",
                        ]
                    ),
                    0,
                )
            self.assertIn("has_motion=1", output.getvalue())


if __name__ == "__main__":
    unittest.main()
