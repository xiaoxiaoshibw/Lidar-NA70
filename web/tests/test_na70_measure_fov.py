"""Tests for the NA70 FOV measurement tool (SYNTHETIC PCAP)."""
import importlib.util
import json
from pathlib import Path

import pytest

from test_decoder import payload, make_pcap

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("measure_na70_fov",
                                              ROOT / "tools/measure_na70_fov.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_measure_reports_code_extrema_and_unverified_counts(tmp_path):
    # 200 packets = 2 frames.  Frame 0 sweeps pitch/yaw codes; frame 1 widens
    # the session envelope and contributes invalid-code rays.
    packets = []
    for i in range(100):
        packets.append(payload(distance=1000 + i, pitch=1000 + 2 * i, yaw=2000 + i))
    for i in range(100):
        if i % 4 == 0:
            packets.append(payload(distance=0, pitch=500, yaw=500))          # zero distance
        elif i % 4 == 1:
            packets.append(payload(distance=2000, pitch=0xFFFF, yaw=0xFFFF))  # all-FF angles
        else:
            packets.append(payload(distance=2000, pitch=1200 + i, yaw=1900 + i))
    pcap = make_pcap(tmp_path / "SYNTHETIC-fov.pcap", packets)
    output = tmp_path / "fov" / "fov.json"
    assert module.main(["--pcap", str(pcap), "--output", str(output)]) == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["status"] == "MEASURED"
    assert result["frames"] == 2
    session = result["session"]
    # Frame 0 valid pitch codes: 1000..1298; yaw 2000..2099.
    # Frame 1 valid rays are i%4 in {2,3}: pitch 1202..1299; yaw 1902..2097.
    assert session["pitch_code_min"] == 1000
    assert session["pitch_code_max"] == 1299
    assert session["azimuth_code_min"] == 1902
    assert session["azimuth_code_max"] == 2099
    assert session["pitch_span_deg"] == pytest.approx((1299 - 1000) * 0.0258)
    assert session["azimuth_span_deg"] == pytest.approx((2099 - 1902) * 0.0432)
    assert session["rays"] == 40000
    assert session["distance_code_zero"] == 25 * 200
    assert session["both_angle_codes_ffff"] == 25 * 200
    assert session["valid_rays"] == (100 + 50) * 200
    assert "下界估计" in result["note"]


def test_measure_refuses_to_overwrite_existing_output(tmp_path):
    pcap = make_pcap(tmp_path / "SYNTHETIC-fov2.pcap", [payload()] * 100)
    output = tmp_path / "fov.json"
    output.write_text("{}", encoding="utf-8")
    assert module.main(["--pcap", str(pcap), "--output", str(output)]) == 2
    assert output.read_text(encoding="utf-8") == "{}"


def test_measure_handles_no_valid_returns(tmp_path):
    pcap = make_pcap(tmp_path / "SYNTHETIC-fov3.pcap", [payload(distance=0)] * 100)
    output = tmp_path / "fov-empty" / "fov.json"
    assert module.main(["--pcap", str(pcap), "--output", str(output)]) == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["session"]["pitch_code_min"] is None
    assert "pitch_span_deg" not in result["session"]
