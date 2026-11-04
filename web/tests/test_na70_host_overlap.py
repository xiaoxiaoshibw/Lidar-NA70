import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


spec = importlib.util.spec_from_file_location("audit_na70_overlap", Path(__file__).resolve().parents[2] / "tools/audit_na70_overlap.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_observed_overlap_and_timestamp_rejection(tmp_path, monkeypatch):
    scan = SimpleNamespace(frames=[{"packet_capture_times": [100.0, 101.0, 102.0]}], invalid_packets=0, warnings=[])
    monkeypatch.setattr(module, "scan_pcap", lambda _: scan)
    chunks = tmp_path / "chunks.jsonl"
    chunks.write_text("".join(json.dumps({"t_unix": t}) + "\n" for t in (100.5, 101.5, 102.5)), encoding="utf-8")
    result = module.audit(tmp_path / "unused.pcap", chunks)
    assert result["status"] == "observed"
    assert result["host_overlap_s"] == 1.5
    chunks.write_text('{"t_unix":101}\n{"t_unix":100}\n', encoding="utf-8")
    assert module.audit(tmp_path / "unused.pcap", chunks)["status"] == "failed"
