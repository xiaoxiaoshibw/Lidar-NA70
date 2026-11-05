"""SYNTHETIC: Qt workflow + real recorder/decoder, using fake serial and loopback UDP."""
import csv
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from na70_associate import associate, bracket, load_batches  # noqa: E402
from test_na70_capture import FakeSerial, build_frame  # noqa: E402
from test_decoder import make_pcap  # noqa: E402
from test_thin_wire import timed_payload  # noqa: E402


def make_session(tmp_path, times=(1000., 1000.05, 1000.1)):
    session = tmp_path / "source"
    session.mkdir()
    (session / "raw").mkdir()
    make_pcap(session / "raw/lidar.pcap", [timed_payload(i) for i in range(101)])
    rows = [{"t_unix": 1700000000 + (t - 1000), "segment": 0, "tid": i * 2 + j}
            for i, t in enumerate(times) for j in range(2)]
    (session / "imu_frames.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    (session / "manifest.json").write_text('{"status":"degraded"}', encoding="utf-8")
    return session


def test_packet_brackets_preserve_all_batch_rows_and_original_indices(tmp_path):
    # make_pcap emits host times 1700000000 + packet_index * .001.
    session = make_session(tmp_path)
    manifest = (session / "manifest.json").read_bytes()
    output = tmp_path / "paired"
    report = associate(session, output=output)
    assert report["claims"]["time_sync_validated"] is False
    assert report["claims"]["R_IL"] is None
    assert report["imu_batches"] == 3 and report["imu_frames"] == 6
    assert report["lidar_packets"] == 101
    assert (session / "manifest.json").read_bytes() == manifest
    with (output / "packet_imu.csv").open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 101
    assert rows[1]["previous_line_first"] == "1"
    assert rows[1]["previous_line_last"] == "2"
    assert rows[1]["next_line_first"] == "3"
    assert rows[1]["next_line_last"] == "4"
    assert rows[100]["packet_index"] == "100" and rows[100]["frame_index"] == "1"
    assert rows[50]["previous_batch"] == rows[50]["next_batch"] == "1"
    with pytest.raises(FileExistsError):
        associate(session, output=output)
    with pytest.raises(ValueError, match="原始会话"):
        associate(session, output=session / "derived")


def test_brackets_flag_gaps_segments_sequence_and_outside(tmp_path):
    session = make_session(tmp_path, times=(1000., 1000.05, 1001.))
    batches, _ = load_batches(session / "imu_frames.jsonl")
    times = [b["host_unix_s"] for b in batches]
    origin = times[0]
    assert bracket(origin - 1, batches, times, .1)[2] == "outside_imu_range"
    assert bracket(origin + 2, batches, times, .1)[2] == "outside_imu_range"
    assert bracket(origin + .5, batches, times, .1)[2] == "host_gap"
    batches[1]["segment"] = 1
    assert bracket(origin + .01, batches, times, .1)[2] == "segment_break"
    batches[1]["segment"] = 0
    batches[1]["follows_previous"] = False
    assert bracket(origin + .01, batches, times, .1)[2] == "sequence_break"


def test_partial_association_is_explicit(tmp_path):
    session = make_session(tmp_path, times=(1000., 1000.001, 1001.))
    report = associate(session, output=tmp_path / "partial")
    assert report["status"] == "PARTIAL_HOST_ASSOCIATION"
    assert report["packet_status_counts"]["host_gap"] > 0
    assert report["claims"]["sampling_error_bound_s"] is None


@pytest.mark.parametrize("value", [999., float("nan"), True])
def test_bad_imu_time_keeps_failure_report(tmp_path, value):
    session = make_session(tmp_path)
    with (session / "imu_frames.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"t_unix": value, "segment": 0, "tid": 6}) + "\n")
    output = tmp_path / "failed"
    with pytest.raises(ValueError):
        associate(session, output=output)
    assert json.loads((output / "association.json").read_text("utf-8"))["status"] == "failed"
    assert (session / "raw/lidar.pcap").is_file()


def test_backward_lidar_time_is_rejected(tmp_path):
    import struct
    session = make_session(tmp_path)
    pcap = session / "raw/lidar.pcap"
    data = bytearray(pcap.read_bytes())
    first_record_size = 16 + struct.unpack_from("<I", data, 24 + 8)[0]
    struct.pack_into("<II", data, 24 + first_record_size, 1699999999, 0)
    pcap.write_bytes(data)
    with pytest.raises(ValueError, match="雷达主机时间"):
        associate(session, output=tmp_path / "failed")


@pytest.fixture(scope="module")
def qt_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
    from PySide6.QtCore import QCoreApplication, QEvent
    for widget in app.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def wait_for(qt_app, predicate, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if predicate():
            return
        time.sleep(.01)
    pytest.fail("Qt workflow timed out")


@pytest.mark.parametrize("early_stop", [False, True])
def test_qt_records_stops_and_associates(qt_app, tmp_path, early_stop):
    from PySide6.QtCore import QSettings
    from na70_qt_capture import CaptureWindow, CaptureWorker, load_config
    from na70_core.capture.lidar_udp import UdpLidarCapture
    from na70_core.capture.runner import CaptureRunner

    class StreamingSerial(FakeSerial):
        def __init__(self):
            super().__init__([])
            self.tid = 0

        def read(self, size):
            time.sleep(.005)
            self.tid += 1
            return build_frame(self.tid)

    cfg = {**load_config(), "session_root": str(tmp_path / "sessions"), "imu_port": "fake",
           "duration_s": 1.2 if not early_stop else 20, "poll_interval_s": .02,
           "min_free_bytes": 0}
    sending = threading.Event()
    sender_thread = None

    def factory(session):
        nonlocal sender_thread
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(.1)
        port = receiver.getsockname()[1]
        lidar = UdpLidarCapture(session, port=2368, socket_factory=lambda: receiver)

        def send():
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                while not sending.wait(.003):
                    sender.sendto(timed_payload(1.0), ("127.0.0.1", port))

        sender_thread = threading.Thread(target=send)
        sender_thread.start()
        return CaptureRunner(session, imu={"port": "fake", "serial_factory": lambda *_: StreamingSerial()},
                             lidar=lidar, poll_interval=.02)

    window = CaptureWindow(cfg, settings=QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat))
    worker = CaptureWorker(cfg, runner_factory=factory, parent=window)
    outcomes = []
    worker.outcome.connect(outcomes.append)
    window.begin_worker(worker)
    try:
        if early_stop:
            wait_for(qt_app, lambda: "录制中" in window.state.text())
            assert not window.start_button.isEnabled()
            window.close()  # Close requests stop and waits for saving + association.
        wait_for(qt_app, lambda: window.worker is None)
        assert outcomes and outcomes[0]["ok"]
        result = outcomes[0]
        assert result["report"]["packet_status_counts"]["host_bracketed"] > 0
        assert Path(window.session_path, "manifest.json").is_file()
        assert Path(window.output_path, "packet_imu.csv").is_file()
        assert window.open_output.isEnabled()
    finally:
        sending.set()
        if sender_thread:
            sender_thread.join(2)
        if window.worker:
            window.worker.requestInterruption()
            window.worker.wait(10000)
        window.close()


def test_qt_failed_capture_keeps_files_and_reenables_start(qt_app, tmp_path):
    from PySide6.QtCore import QSettings
    from na70_qt_capture import CaptureWindow, CaptureWorker, load_config

    def fail(session):
        session.file("raw/retained.bin").write_bytes(b"SYNTHETIC")
        raise OSError("SYNTHETIC bind failure")

    cfg = {**load_config(), "session_root": str(tmp_path / "sessions"), "min_free_bytes": 0}
    window = CaptureWindow(cfg, settings=QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat))
    window.begin_worker(CaptureWorker(cfg, runner_factory=fail, parent=window))
    wait_for(qt_app, lambda: window.worker is None)
    assert "任务失败" in window.state.text()
    assert window.start_button.isEnabled()
    assert Path(window.session_path, "raw/retained.bin").read_bytes() == b"SYNTHETIC"
    assert json.loads(Path(window.session_path, "manifest.json").read_text("utf-8"))["status"] == "failed"
    window.close()
