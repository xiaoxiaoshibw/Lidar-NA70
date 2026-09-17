"""SYNTHETIC packet/sweep regression tests."""
from pathlib import Path
import struct

import numpy as np

from backend.decoder import scan_pcap
from backend.sweeps import (
    SweepBuilder, SweepConfig, SweepPacket, build_sweeps, packets_from_scan,
)
from test_decoder import make_pcap, payload


def packet(index, capture_time=None, device_time=None, distance=1000,
           pitch=2200, yaw=None, sequence_id=None, empty=False, **kwargs):
    capture_time = index * 0.01 if capture_time is None else capture_time
    device_time = 100 + capture_time if device_time is None else device_time
    yaw = 10 + index * 8 if yaw is None else yaw
    body = payload(distance=0 if empty else distance, pitch=pitch, yaw=yaw)
    return SweepPacket(
        source_index=index,
        sequence_id=index if sequence_id is None else sequence_id,
        capture_time=capture_time,
        device_time=device_time,
        payload=body,
        payload_offset=1000 + index * 1500,
        factory_code=kwargs.get("factory_code", 0),
        pitch_offset=kwargs.get("pitch_offset", 0.0),
        yaw_offset=kwargs.get("yaw_offset", 0.0),
    )


def config(**kwargs):
    values = dict(min_packets=2, min_points=400, max_packets=20,
                  max_duration=0.5, pitch_bin_size=4, yaw_bin_size=8,
                  target_angular_bins=2, min_angular_coverage=1.0)
    values.update(kwargs)
    return SweepConfig(**values)


def test_normal_complete_angular_coverage_and_raw_fields():
    sweeps = SweepBuilder(config()).build([
        packet(0, factory_code=0x1001, pitch_offset=1.25),
        packet(1, factory_code=0x2002, pitch_offset=1.25, yaw_offset=-2.5),
    ])
    assert len(sweeps) == 1 and sweeps[0].complete
    assert sweeps[0].completion_reason == "angular_coverage"
    assert sweeps[0].codes.shape == (400, 4)
    np.testing.assert_array_equal(sweeps[0].codes[0], [1000, 2200, 10, 77])
    metadata = sweeps[0].metadata(config())
    assert metadata["point_time_semantics"] == "packet_time_only"
    assert metadata["packets"][1] == {
        "source_index": 1, "sequence_id": 1, "capture_time": 0.01,
        "device_time": 100.01, "payload_offset": 2500, "point_start": 200,
        "point_count": 200, "factory_code": 0x2002, "pitch_offset": 1.25,
        "yaw_offset": -2.5, "input_anomalies": [],
    }


def test_insufficient_coverage_uses_max_time_window():
    cfg = config(target_angular_bins=100, min_angular_coverage=0.9,
                 max_duration=0.2)
    sweeps = SweepBuilder(cfg).build([
        packet(0, capture_time=1.0), packet(1, capture_time=1.1),
        packet(2, capture_time=1.21),
    ])
    assert sweeps[0].completion_reason == "max_time_window"
    assert not sweeps[0].complete and len(sweeps[0].packets) == 3


def test_end_of_stream_preserves_incomplete_tail():
    sweeps = SweepBuilder(config(min_packets=3)).build([packet(0), packet(1)])
    assert len(sweeps) == 1
    assert sweeps[0].completion_reason == "end_of_stream"
    assert not sweeps[0].complete


def test_capture_and_device_time_backwards_are_explicit():
    cfg = config(min_packets=10, min_points=2000)
    sweeps = SweepBuilder(cfg).build([
        packet(0, capture_time=1.0, device_time=10.0),
        packet(1, capture_time=1.1, device_time=10.1),
        packet(2, capture_time=0.9, device_time=9.9),
    ])
    assert sweeps[0].completion_reason == "capture_time_backwards"
    assert any(item["kind"] == "capture_backwards" for item in sweeps[0].anomalies)
    assert any(item["kind"] == "device_backwards" for item in sweeps[1].anomalies)


def test_packet_gap_and_duplicate_are_marked_without_duplicate_points():
    cfg = config(min_packets=10, min_points=2000)
    builder = SweepBuilder(cfg)
    original = packet(0, sequence_id=50)
    duplicate = packet(1, sequence_id=50)
    gap = packet(2, sequence_id=53)
    sweeps = builder.build([original, duplicate, gap])
    assert len(sweeps[0].packets) == 2
    kinds = {item["kind"] for item in sweeps[0].anomalies}
    assert kinds == {"duplicate_packet", "packet_gap"}
    assert sweeps[0].codes.shape == (400, 4)


def test_empty_packets_are_not_counted_as_coverage():
    cfg = config(min_points=1, target_angular_bins=1, max_duration=0.1)
    sweeps = SweepBuilder(cfg).build([
        packet(0, capture_time=0.0, empty=True),
        packet(1, capture_time=0.11, empty=True),
    ])
    assert sweeps[0].metadata(cfg)["valid_point_count"] == 0
    assert sweeps[0].metadata(cfg)["angular_coverage"] == 0
    assert sweeps[0].completion_reason == "max_time_window"


def test_batch_and_streaming_boundaries_are_identical():
    packets = [packet(i) for i in range(7)]
    batch = SweepBuilder(config()).build(packets)
    streaming_builder = SweepBuilder(config())
    streaming = []
    for item in packets:
        streaming.extend(streaming_builder.add(item))
    streaming.extend(streaming_builder.finish())
    assert [(s.completion_reason, [p.source_index for p in s.packets]) for s in batch] == [
        (s.completion_reason, [p.source_index for p in s.packets]) for s in streaming
    ]
    for left, right in zip(batch, streaming):
        np.testing.assert_array_equal(left.codes, right.codes)


def test_fresh_pcap_index_retains_packet_times_and_builds_sweeps(tmp_path):
    capture = make_pcap(tmp_path / "packets.pcap", [payload(yaw=i * 8) for i in range(4)])
    scan = scan_pcap(capture)
    assert scan.frames[0]["packet_capture_times"] == [1700000000.0, 1700000000.001,
                                                       1700000000.002, 1700000000.003]
    packets = list(packets_from_scan(capture, scan.frames))
    assert [item.source_index for item in packets] == [0, 1, 2, 3]
    sweeps = build_sweeps(capture, scan.frames, config())
    assert [[p.source_index for p in sweep.packets] for sweep in sweeps] == [[0, 1], [2, 3]]
    assert all(sweep.complete for sweep in sweeps)

