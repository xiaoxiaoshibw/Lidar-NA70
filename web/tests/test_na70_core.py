"""SYNTHETIC contract tests against the sole production NA70 decoder."""
import hashlib

import numpy as np
import pytest

from backend import decoder
from backend.thin_wire import decode_window, load_config, packet_timeline, select_window
from na70_core import RayBatch, direction_known_from_codes
from test_decoder import make_pcap
from test_thin_wire import timed_payload


def test_ray_batch_preserves_decoder_and_w1_source_identity(tmp_path):
    path = make_pcap(tmp_path / "SYNTHETIC-observations.pcap", [
        timed_payload(10 + i * .001, distances=(1250, 0, 12500), yaw=0)
        for i in range(101)
    ])
    cfg = load_config()
    scan = decoder.scan_pcap(path)
    selected, _ = select_window(packet_timeline(scan, cfg), 1, None, cfg)
    arrays = decode_window(path, scan, selected, cfg)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    batch = RayBatch.from_decoded(
        capture_sha256=sha, coordinate_frame="lidar_link",
        install_direction=cfg["install_direction"], **{key: arrays[key] for key in (
            "codes", "points", "frame_indices", "packet_indices",
            "point_indices", "device_times", "capture_times")},
    )
    assert batch.ray_count == 200
    np.testing.assert_array_equal(batch.points, decoder.decode_frame(path, scan.frames[1]))
    np.testing.assert_array_equal(batch.codes, decoder.decode_frame_codes(path, scan.frames[1]))
    np.testing.assert_array_equal(batch.valid_return, arrays["valid_mask"])
    np.testing.assert_array_equal(batch.direction_known, arrays["direction_known_mask"])
    np.testing.assert_array_equal(batch.source_refs(batch.valid_return)[:2], [[1, 100, 0], [1, 100, 2]])
    assert batch.device_times[0] == batch.device_times[199]
    assert batch.direction_known.sum() == batch.valid_return.sum()
    arrays["codes"][0, 0] = 0
    assert batch.codes[0, 0] == 1250  # Published arrays do not alias the producer.
    with pytest.raises(ValueError):
        batch.codes[0, 0] = 0


def test_direction_status_and_invalid_contracts():
    codes = np.array([[0, 0, 0, 0], [0, 1, 0, 0], [0, 0, 2, 0],
                      [100, 0, 0, 17]], dtype=np.uint16)
    assert direction_known_from_codes(codes).tolist() == [False, True, True, True]
    common = dict(capture_sha256="a" * 64, coordinate_frame="lidar_link",
                  install_direction=1, codes=codes,
                  points=np.zeros((4, 4), dtype=np.float32),
                  frame_indices=np.zeros(4, dtype=np.int32),
                  packet_indices=np.zeros(4, dtype=np.int64),
                  point_indices=np.arange(4, dtype=np.int32),
                  device_times=np.array([1.0, np.nan, 1.0, 1.0]),
                  capture_times=np.ones(4))
    batch = RayBatch.from_decoded(**common)
    assert batch.valid_return.tolist() == [False, False, False, True]
    assert batch.direction_known.tolist() == [False, True, True, True]
    with pytest.raises(ValueError, match="mask"):
        batch.source_refs(np.ones(3, dtype=bool))
    with pytest.raises(ValueError, match="SHA-256"):
        RayBatch.from_decoded(**{**common, "capture_sha256": "bad"})
    with pytest.raises(ValueError, match="point_indices"):
        RayBatch.from_decoded(**{**common, "point_indices": np.array([0, 1, 2, 20_000])})
    with pytest.raises(ValueError, match="device_times"):
        RayBatch.from_decoded(**{**common, "device_times": np.array([1.0, np.inf, 1.0, 1.0])})
