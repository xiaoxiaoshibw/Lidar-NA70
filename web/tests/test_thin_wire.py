"""SYNTHETIC protocol fixtures validate W1 tooling, not physical thin-wire capability."""
import json
import struct

import numpy as np
import pytest

from backend import decoder
from backend.thin_wire import (decode_window, load_config, packet_timeline, reference_distances,
                               run_audit, select_window, window_metrics)
from test_decoder import make_pcap


def timed_payload(seconds, distances=(1250, 12500, 0), yaw=0):
    # 20 m, 200 m and zero-distance rays; preserves different intensities.
    rays = b"".join(struct.pack(">HHHB", distances[i % len(distances)], 0, yaw, i % 256) for i in range(200))
    if seconds is None:
        tail = b"\0" * 6 + b"\xff\xff"
    else:
        milliseconds = round(seconds * 1000)
        tail = (milliseconds // 1000).to_bytes(6, "big") + (milliseconds % 1000).to_bytes(2, "big")
    return rays + tail + b"\0\0"


def capture(tmp_path, times, name="raw.pcap", **kwargs):
    return make_pcap(tmp_path / name, [timed_payload(t, **kwargs) for t in times])


def config():
    cfg = load_config()
    cfg["acquisition"]["data_label"] = "SYNTHETIC"
    cfg["target_roi"] = [-1, 1, -201, -199, -1, 1]
    cfg["background_rois"] = [[-1, 1, -21, -19, -1, 1]]
    cfg["target_reference"] = {
        "endpoints_sensor_m": [[-.5, -200, 0], [.5, -200, 0]],
        "corridor_radius_m": .1, "position_uncertainty_m": .05,
        "source": "SYNTHETIC protocol fixture, not a surveyed wire", "review_status": "unreviewed",
    }
    return cfg


def test_unfiltered_ranges_raw_codes_and_provenance(tmp_path):
    path = capture(tmp_path, [10 + i * .001 for i in range(110)], yaw=100)
    scan, cfg = decoder.scan_pcap(path), config()
    packets, _ = select_window(packet_timeline(scan, cfg), 1, None, cfg)
    arrays = decode_window(path, scan, packets, cfg)
    np.testing.assert_array_equal(arrays["points"], decoder.decode_frame(path, scan.frames[1]))
    np.testing.assert_array_equal(arrays["codes"], decoder.decode_frame_codes(path, scan.frames[1]))
    assert arrays["point_indices"].tolist() == list(range(2000))
    assert np.all(arrays["frame_indices"] == 1)
    assert arrays["packet_indices"].min() == 100
    np.testing.assert_allclose(np.linalg.norm(arrays["points"][:2, :3], axis=1), [20, 200], atol=1e-4)
    assert arrays["points"][0, 2] < 0  # No absolute-height gate.
    assert arrays["valid_mask"][:3].tolist() == [True, True, False]
    assert arrays["direction_known_mask"][:3].tolist() == [True, True, True]  # Zero range still has yaw code.
    metrics = window_metrics(arrays, cfg)
    assert metrics["direction_known_count"] == int(arrays["direction_known_mask"].sum())
    assert metrics["direction_known_count"] + metrics["direction_unknown_count"] == metrics["ray_count"]
    assert arrays["device_times"][0] == arrays["device_times"][199] == pytest.approx(10.100)


def test_seconds_window_clips_packets_and_keeps_original_indices(tmp_path):
    path = capture(tmp_path, [10 + i * .001 for i in range(200)])
    scan, cfg = decoder.scan_pcap(path), config()
    packets, metadata = select_window(packet_timeline(scan, cfg), 1, .05, cfg)
    assert [p.index for p in packets] == list(range(150, 200))
    assert metadata["time_quality"] == "ok" and not metadata["partial_window"]
    assert metadata["actual_span_s"] == pytest.approx(.049)
    arrays = decode_window(path, scan, packets, cfg)
    assert arrays["point_indices"][0] == 10000
    assert arrays["point_indices"][-1] == 19999
    assert np.all(arrays["device_times"] > metadata["requested_start_time_exclusive"])


@pytest.mark.parametrize("times,expected,reason", [
    ([1, 1.001, 2, 2.001], [2, 3], "device_gap"),
    ([1, 1.001, .5, .501], [2, 3], "device_backwards"),
    ([1, None, 1.002, 1.003], [2, 3], "after_invalid_device_time"),
])
def test_no_accumulation_across_bad_time(tmp_path, times, expected, reason):
    path, cfg = capture(tmp_path, times), config()
    packets, metadata = select_window(packet_timeline(decoder.scan_pcap(path), cfg), 0, .5, cfg)
    assert [p.index for p in packets] == expected
    assert metadata["partial_window"] and metadata["segment_start_reason"] == reason
    assert metadata["actual_span_s"] == pytest.approx(.001)


def test_invalid_end_is_not_capture_time_fallback(tmp_path):
    path, cfg = capture(tmp_path, [1, None]), config()
    scan = decoder.scan_pcap(path)
    timeline = packet_timeline(scan, cfg)
    packets, metadata = select_window(timeline, 0, .5, cfg)
    assert packets == [] and metadata["time_quality"] == "invalid_end_time"
    arrays = decode_window(path, scan, packets, cfg)
    assert arrays["points"].shape == (0, 4)
    assert window_metrics(arrays, cfg)["wire_return_status"] == "NOT_EVALUATED"
    packets, metadata = select_window(timeline, 0, None, cfg)
    arrays = decode_window(path, scan, packets, cfg)
    assert metadata["time_quality"] == "invalid_device_time"
    assert np.isnan(arrays["device_times"][200:]).all()
    assert len(arrays["points"]) == 400


def test_equal_packet_times_are_retained_not_counted_as_extra_elapsed_time(tmp_path):
    path, cfg = capture(tmp_path, [1, 1, 1.001]), config()
    packets, metadata = select_window(packet_timeline(decoder.scan_pcap(path), cfg), 0, .5, cfg)
    assert len(packets) == 3 and metadata["equal_adjacent_device_times"] == 1
    assert metadata["actual_span_s"] == pytest.approx(.001)
    assert metadata["partial_window"]


def test_roi_background_and_reference_do_not_confirm_wire(tmp_path):
    path, cfg = capture(tmp_path, [1]), config()
    scan = decoder.scan_pcap(path)
    packets, _ = select_window(packet_timeline(scan, cfg), 0, None, cfg)
    arrays = decode_window(path, scan, packets, cfg)
    stats = window_metrics(arrays, cfg)
    assert stats["target_roi"]["point_count"] == stats["reference_corridor"]["point_count"] == 67
    assert stats["background_rois"][0]["point_count"] == 67
    assert stats["zero_distance_count"] == 66
    assert stats["confirmed_target_return_count"] is None
    assert stats["wire_return_status"] == "NOT_EVALUATED"
    assert not np.any(arrays["target_roi_mask"] & arrays["background_roi_mask"])
    distances = reference_distances(np.array([[0, -200, 0], [2, -200, 0], [0, -200, .3]]), cfg["target_reference"])
    np.testing.assert_allclose(distances, [0, 1.5, .3])


def test_new_background_does_not_make_old_target_points_fresh(tmp_path):
    path = make_pcap(tmp_path / "age.pcap", [timed_payload(1, distances=(12500,)), timed_payload(1.01, distances=(1250,))])
    cfg, scan = config(), decoder.scan_pcap(path)
    packets, metadata = select_window(packet_timeline(scan, cfg), 0, .5, cfg)
    arrays = decode_window(path, scan, packets, cfg)
    stats = window_metrics(arrays, cfg, metadata["end_device_time"])
    assert stats["reference_corridor"]["latest_source_age_s"] == pytest.approx(.01)
    assert stats["background_rois"][0]["latest_source_age_s"] == 0
    assert stats["confirmed_target_return_count"] is None


@pytest.mark.parametrize("key,value", [
    ("window_seconds", [float("nan")]), ("window_seconds", [-1]),
    ("window_seconds", [.5, .5]), ("include_single_frame", "yes"),
    ("max_window_points", 0), ("max_packet_gap_s", 0),
    ("install_direction", 3), ("target_roi", [0, 1, 0, 1, 2, 1]),
    ("render_dpi", float("inf")), ("unknown_option", True),
])
def test_invalid_config(tmp_path, key, value):
    path = tmp_path / "bad.yaml"
    path.write_text(json.dumps({key: value}), "utf-8")
    with pytest.raises(ValueError):
        load_config(path)


def test_overlapping_regions_and_unjustified_static_rejected(tmp_path):
    cfg = config()
    cfg["background_rois"] = [cfg["target_roi"]]
    path = tmp_path / "bad.yaml"
    path.write_text(json.dumps(cfg), "utf-8")
    with pytest.raises(ValueError, match="overlap"):
        load_config(path)
    cfg = config()
    cfg["acquisition"]["static_status"] = "confirmed"
    path.write_text(json.dumps(cfg), "utf-8")
    with pytest.raises(ValueError, match="evidence"):
        load_config(path)


def test_budget_rejects_without_truncation(tmp_path):
    path, cfg = capture(tmp_path, [1, 1.001]), config()
    cfg["max_window_points"] = 200
    with pytest.raises(ValueError, match="no truncation"):
        select_window(packet_timeline(decoder.scan_pcap(path), cfg), 0, .5, cfg)


def test_evidence_export_independent_inputs_and_no_overwrite(tmp_path):
    primary = capture(tmp_path, [1, 1.001])
    background = capture(tmp_path, [100, 100.001], "background.pcap", distances=(1250,))
    cfg = config()
    cfg["window_seconds"] = [.5]
    out = tmp_path / "evidence"
    manifest = run_audit(primary, out, cfg, [0], background_pcap=background, render=False)
    assert manifest["status"] == "COMPLETED" and manifest["wire_return_status"] == "NOT_EVALUATED"
    assert "packages/na70_core/na70_core/geometry.py" in manifest["source_hashes"]
    assert "packages/na70_core/na70_core/observations.py" in manifest["source_hashes"]
    assert manifest["window_count"] == 4
    rows = [json.loads(line) for line in (out / "window_metrics.jsonl").read_text("utf-8").splitlines()]
    assert rows[0]["input_sha256"] != rows[2]["input_sha256"]
    assert rows[0]["target_roi"]["point_count"] == 134
    assert rows[2]["target_roi"]["point_count"] == 0
    assert rows[2]["actual_start_device_time"] == 100
    with np.load(out / rows[0]["raw_file"], allow_pickle=False) as raw:
        assert len(raw["points"]) == 400
        assert len(np.unique(np.column_stack((raw["frame_indices"], raw["point_indices"])), axis=0)) == 400
        np.testing.assert_array_equal(raw["points"], decoder.decode_frame(primary, decoder.scan_pcap(primary).frames[0]))
    assert manifest["artifacts"][rows[0]["raw_file"]] == rows[0]["raw_sha256"]
    with pytest.raises(FileExistsError):
        run_audit(primary, out, cfg, [0], render=False)


def test_duplicate_end_frames_and_duplicate_background_preserve_failure(tmp_path):
    primary, cfg = capture(tmp_path, [1]), config()
    with pytest.raises(ValueError, match="unique"):
        run_audit(primary, tmp_path / "duplicate", cfg, [0, 0], render=False)
    assert json.loads((tmp_path / "duplicate/manifest.json").read_text())["status"] == "FAILED"
    duplicate = tmp_path / "copy.pcap"
    duplicate.write_bytes(primary.read_bytes())
    cfg["window_seconds"] = []
    with pytest.raises(ValueError, match="duplicates"):
        run_audit(primary, tmp_path / "same-content", cfg, [0], background_pcap=duplicate, render=False)


def test_render_artifacts_are_bound_to_unsampled_data(tmp_path):
    path, cfg = capture(tmp_path, [1]), config()
    cfg["window_seconds"] = []
    cfg["render_max_points"] = 20
    out = tmp_path / "render"
    manifest = run_audit(path, out, cfg, [0])
    assert len(manifest["windows"][0]["figures"]) == 4
    for relative in manifest["windows"][0]["figures"]:
        assert (out / relative).read_bytes().startswith(b"\x89PNG")
    with np.load(out / manifest["windows"][0]["raw_file"]) as arrays:
        assert len(arrays["points"]) == 200
