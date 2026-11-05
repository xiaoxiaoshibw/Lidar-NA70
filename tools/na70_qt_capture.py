"""NA70 + YESENSE 本机 Qt 录制器；无需 Web 服务，不向设备写配置。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket
import sys
import time
import traceback

from na70_associate import DEFAULT_CONFIG, ROOT, associate
from na70_core.capture.lidar_udp import UdpLidarCapture
from na70_core.capture.runner import CaptureRunner
from na70_core.capture.session import CaptureSession
from PySide6.QtCore import QSettings, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPlainTextEdit,
    QProgressBar, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)
from serial.tools.list_ports import comports


def load_config(path=DEFAULT_CONFIG):
    # JSON is a YAML subset, matching tools/lio/*.yaml without another dependency.
    config = json.loads(Path(path).read_text("utf-8"))
    if config["lidar_port"] != 2368:
        raise ValueError("唯一正式解码器仅支持 NA70 UDP 2368")
    if not 1 <= config["duration_s"] <= config["max_duration_s"] <= 1800:
        raise ValueError("录制时长配置须在 1–1800 秒内")
    if config["min_free_bytes"] < 0 or config["poll_interval_s"] <= 0:
        raise ValueError("磁盘/轮询配置无效")
    if not 0 < config["max_host_batch_gap_s"] < float("inf"):
        raise ValueError("主机批次间隔配置无效")
    return config


class CaptureWorker(QThread):
    progress = Signal(dict)
    message = Signal(str)
    outcome = Signal(dict)

    def __init__(self, config, *, existing=None, runner_factory=None, parent=None):
        super().__init__(parent)
        self.config = dict(config)
        self.existing = Path(existing) if existing else None
        self.runner_factory = runner_factory

    def run(self):
        session = None
        runner = None
        result = {}
        try:
            if self.existing:
                path = self.existing
                if not (path / "manifest.json").is_file():
                    raise ValueError("请选择已完成录制、有 manifest.json 的会话，不能关联正在写入的数据")
            else:
                cfg = self.config
                session = CaptureSession(
                    cfg["session_root"], task="QT-DUAL-CAPTURE",
                    session_id=time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1000000:06d}",
                    operator={"application": "na70-qt-capture-v1", "config": cfg},
                    min_free_bytes=cfg["min_free_bytes"])
                path = session.path
                self.progress.emit({"phase": "starting", "session": str(path)})
                self.message.emit(f"原始数据保存到：{path}")
                if self.runner_factory:
                    runner = self.runner_factory(session)
                else:
                    runner = CaptureRunner(
                        session, imu={"port": cfg["imu_port"], "baud": cfg["imu_baud"]},
                        lidar=UdpLidarCapture(session, port=cfg["lidar_port"]),
                        poll_interval=cfg["poll_interval_s"])
                runner.start()
                manifest = runner.run(
                    cfg["duration_s"], should_stop=self.isInterruptionRequested,
                    tick_interval=cfg["poll_interval_s"],
                    on_tick=lambda status, remaining: self.progress.emit(
                        {"phase": "recording", "session": str(path),
                         "remaining_s": remaining, "devices": status}))
                quality = json.loads((path / "quality.json").read_text("utf-8"))
                self.progress.emit({"phase": "finalized", "devices": {
                    "imu": quality.get("imu") or {}, "lidar": quality.get("lidar") or {}}})
                result["capture_status"] = manifest["status"]
                self.message.emit(f"录制质量：{manifest['status']}")
                for check in quality.get("checks", []):
                    if not check["ok"]:
                        self.message.emit(f"需留意：{check['detail']}")
            result["session"] = str(path)
            self.progress.emit({"phase": "associating", "session": str(path)})
            report = associate(path, max_gap_s=self.config["max_host_batch_gap_s"],
                               progress=self.message.emit)
            result.update(report=report, ok=(report["status"] in ("HOST_ASSOCIATED", "PARTIAL_HOST_ASSOCIATION")
                                            and result.get("capture_status", report.get("capture_status")) != "failed"))
        except Exception as exc:
            result.update(ok=False, error=f"{type(exc).__name__}: {exc}")
            self.message.emit(traceback.format_exc())
            if session is not None:
                result["session"] = str(session.path)
                if not session._closed:
                    try:
                        if runner is not None:
                            runner._safe_stop()
                        session.event("qt_job_failed", error=str(exc))
                        session.finalize(status="failed", quality={"status": "failed", "error": str(exc)})
                    except Exception as stop_exc:
                        self.message.emit(f"收尾异常：{stop_exc}；请保留原始目录。")
                    finally:
                        session.close()
        self.outcome.emit(result)


class CaptureWindow(QMainWindow):
    def __init__(self, config, *, settings=None):
        super().__init__()
        self.config = config
        self.settings = settings if settings is not None else QSettings("NA70", "CaptureQt")
        self.worker = None
        self.closing = False
        self.session_path = None
        self.output_path = None
        self.setWindowTitle("NA70 · 雷达与 IMU 一键录制")
        self.resize(1000, 820)
        body = QWidget()
        self.setCentralWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(28, 22, 28, 22)
        layout.setSpacing(14)
        heading = QLabel("雷达 + IMU  /  一键录制")
        heading.setObjectName("heading")
        layout.addWidget(heading)
        subtitle = QLabel("NA70 网口 · YESENSE USB 串口    同次录制 → 原始数据保存 → 自动生成时间关联表")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        form_box = QFrame()
        form_box.setObjectName("panel")
        form = QFormLayout(form_box)
        form.setContentsMargins(18, 14, 18, 14)
        self.port = QComboBox()
        self.port.setEditable(True)
        self.port.setMinimumWidth(400)
        self.port.setObjectName("imuPort")
        self.rescan = QPushButton("刷新串口")
        self.rescan.clicked.connect(self.refresh_ports)
        row = QHBoxLayout()
        row.addWidget(self.port, 1)
        row.addWidget(self.rescan)
        form.addRow("IMU 串口", row)
        self.baud = QComboBox()
        self.baud.setEditable(True)
        self.baud.addItems(["460800", "115200", "921600"])
        self.baud.setCurrentText(str(self.settings.value("baud", config["imu_baud"])))
        form.addRow("波特率", self.baud)
        self.duration = QSpinBox()
        self.duration.setRange(1, config["max_duration_s"])
        self.duration.setSuffix(" 秒")
        self.duration.setValue(int(self.settings.value("duration", config["duration_s"])))
        form.addRow("录制时长", self.duration)
        self.root = QLineEdit(str(self.settings.value("root", ROOT / config["session_root"])))
        self.root.setObjectName("sessionRoot")
        browse = QPushButton("选择目录")
        browse.clicked.connect(self.choose_root)
        row = QHBoxLayout()
        row.addWidget(self.root, 1)
        row.addWidget(browse)
        form.addRow("保存位置", row)
        self.gap = QDoubleSpinBox()
        self.gap.setRange(0.001, 60)
        self.gap.setDecimals(3)
        self.gap.setSuffix(" 秒（仅标记主机接收空洞）")
        self.gap.setValue(float(self.settings.value("gap", config["max_host_batch_gap_s"])))
        form.addRow("关联间隔门限", self.gap)
        try:
            ips = ", ".join(sorted(set(socket.gethostbyname_ex(socket.gethostname())[2])))
        except OSError:
            ips = "无法读取，请检查网卡"
        network = QLabel(f"雷达 UDP 2368；请将设备目的地址设为本机有线 IP。\n本机 IPv4 候选：{ips}")
        network.setWordWrap(True)
        form.addRow("网络", network)
        layout.addWidget(form_box)
        self.form_box = form_box

        row = QHBoxLayout()
        self.start_button = QPushButton("一键开始录制")
        self.start_button.setObjectName("start")
        self.start_button.setMinimumHeight(48)
        self.start_button.clicked.connect(self.start_capture)
        self.stop_button = QPushButton("停止并保存")
        self.stop_button.setMinimumHeight(48)
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_capture)
        self.existing_button = QPushButton("关联已有会话")
        self.existing_button.clicked.connect(self.open_existing)
        for button in (self.start_button, self.stop_button, self.existing_button):
            row.addWidget(button)
        layout.addLayout(row)

        row = QHBoxLayout()
        self.imu_metric = QLabel("IMU\n0 帧  ·  0 Hz")
        self.lidar_metric = QLabel("雷达\n0 包  ·  0 包/秒")
        for metric in (self.imu_metric, self.lidar_metric):
            metric.setObjectName("metric")
            row.addWidget(metric)
        layout.addLayout(row)
        self.state = QLabel("就绪 · 确认设备连接后点击开始")
        self.state.setWordWrap(True)
        layout.addWidget(self.state)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        layout.addWidget(self.bar)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        self.log.setMinimumHeight(110)
        layout.addWidget(self.log, 1)
        row = QHBoxLayout()
        self.open_session = QPushButton("打开原始会话目录")
        self.open_session.setEnabled(False)
        self.open_session.clicked.connect(lambda: self.open_folder(self.session_path))
        self.open_output = QPushButton("打开关联结果目录")
        self.open_output.setEnabled(False)
        self.open_output.clicked.connect(lambda: self.open_folder(self.output_path))
        row.addWidget(self.open_session)
        row.addWidget(self.open_output)
        layout.addLayout(row)
        limits = QLabel("时间关联：按同一电脑的接收时间配对，保留同批 IMU 全部帧。精确采样同步与空间外参仍待标定。")
        limits.setWordWrap(True)
        limits.setObjectName("limits")
        layout.addWidget(limits)
        self.setStyleSheet("""
            QMainWindow { background: #f3f6fa; }
            QWidget { color: #23364a; font-size: 14px; }
            QLabel#heading { font-size: 27px; font-weight: 700; color: #142d46; }
            QFrame#panel, QLabel#metric { background: white; border: 1px solid #dbe3eb; border-radius: 9px; }
            QLabel#metric { padding: 16px; font-size: 19px; }
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { padding: 6px; background: white; }
            QPushButton { padding: 8px 14px; border: 1px solid #c4d1dd; border-radius: 6px; background: white; }
            QPushButton#start { background: #087f70; color: white; font-weight: bold; }
            QPushButton:disabled { color: #97a5b2; background: #e6ebf0; }
            QPlainTextEdit { background: #142d46; color: #e0eaf4; border: none; border-radius: 6px; padding: 8px; }
            QLabel#limits { color: #715529; }
            QProgressBar { border: none; background: #dce5ed; border-radius: 4px; text-align: center; }
            QProgressBar::chunk { background: #11a38d; border-radius: 4px; }
        """)
        self.refresh_ports()

    def append_log(self, message):
        self.log.appendPlainText(time.strftime("%H:%M:%S  ") + message)

    def selected_port(self):
        if self.port.currentIndex() >= 0 and self.port.currentText() == self.port.itemText(self.port.currentIndex()):
            return self.port.currentData() or ""
        return self.port.currentText().strip()

    def refresh_ports(self):
        current = self.selected_port() or str(self.settings.value("port", ""))
        self.port.clear()
        self.port.addItem("请选择 IMU 串口（也可直接输入 COM 号）", "")
        try:
            ports = sorted(comports(), key=lambda p: p.device)
        except Exception as exc:
            self.append_log(f"串口枚举失败：{exc}")
            return
        for port in ports:
            self.port.addItem(f"{port.device} · {port.description}", port.device)
        names = [p.device for p in ports]
        preferred = current if current in names else self.config["preferred_imu_port"]
        if preferred in names:
            self.port.setCurrentIndex(names.index(preferred) + 1)
        elif len(ports) == 1:
            self.port.setCurrentIndex(1)

    def choose_root(self):
        target = QFileDialog.getExistingDirectory(self, "选择录制保存目录", self.root.text())
        if target:
            self.root.setText(target)

    def start_capture(self):
        try:
            port = self.selected_port()
            baud = int(self.baud.currentText())
            if not port or baud <= 0 or not self.root.text().strip():
                raise ValueError("请选择 IMU 串口，填写正整数波特率和保存目录")
            config = {**self.config, "session_root": str(Path(self.root.text()).resolve()),
                      "imu_port": port, "imu_baud": baud, "duration_s": self.duration.value(),
                      "max_host_batch_gap_s": self.gap.value()}
            for key, value in (("port", port), ("baud", baud), ("root", config["session_root"]),
                               ("duration", self.duration.value()), ("gap", self.gap.value())):
                self.settings.setValue(key, value)
            self.begin_worker(CaptureWorker(config, parent=self))
        except (ValueError, OSError) as exc:
            self.state.setText(f"无法开始：{exc}")
            self.append_log(str(exc))

    def open_existing(self):
        path = QFileDialog.getExistingDirectory(self, "选择包含 imu_frames.jsonl 与雷达 PCAP 的会话", self.root.text())
        if path:
            config = {**self.config, "max_host_batch_gap_s": self.gap.value()}
            self.begin_worker(CaptureWorker(config, existing=path, parent=self))

    def begin_worker(self, worker):
        self.worker = worker
        self.output_path = self.session_path = None
        self.open_session.setEnabled(False)
        self.open_output.setEnabled(False)
        self.form_box.setEnabled(False)
        self.start_button.setEnabled(False)
        self.existing_button.setEnabled(False)
        self.stop_button.setEnabled(not bool(worker.existing))
        self.imu_metric.setText("IMU\n0 帧  ·  0 Hz")
        self.lidar_metric.setText("雷达\n0 包  ·  0 包/秒")
        self.state.setText("正在准备…")
        self.bar.setRange(0, 0)
        worker.progress.connect(self.show_progress)
        worker.message.connect(self.append_log)
        worker.outcome.connect(self.show_outcome)
        worker.finished.connect(self.worker_finished)
        worker.start()

    def stop_capture(self):
        if self.worker and self.worker.isRunning():
            self.worker.requestInterruption()
            self.stop_button.setEnabled(False)
            self.state.setText("正在停止并保存，随后自动生成关联表…")

    def show_progress(self, update):
        if update.get("session"):
            self.session_path = update["session"]
            self.open_session.setEnabled(True)
        devices = update.get("devices")
        if devices:
            imu, lidar = devices.get("imu", {}), devices.get("lidar", {})
            self.imu_metric.setText(f"IMU\n{imu.get('frames', 0):,} 帧  ·  {imu.get('hz', 0)} Hz")
            self.lidar_metric.setText(f"雷达\n{lidar.get('captured', 0):,} 包  ·  {lidar.get('packets_per_sec', 0)} 包/秒")
        phase = update["phase"]
        if phase == "recording":
            remaining = update["remaining_s"]
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(1000 * (1 - remaining / self.worker.config["duration_s"])))
            notes = []
            if not devices["lidar"].get("captured"):
                notes.append("尚无雷达包，请检查目的 IP、网线和端口占用")
            if devices["imu"].get("error"):
                notes.append("IMU：" + devices["imu"]["error"])
            self.state.setText(f"录制中 · 剩余 {remaining:.0f} 秒" + ("\n" + "；".join(notes) if notes else ""))
        elif phase == "associating":
            self.stop_button.setEnabled(False)
            self.bar.setRange(0, 0)
            self.state.setText("原始会话已保存 · 正在自动关联两路数据…")

    def show_outcome(self, result):
        self.bar.setRange(0, 1000)
        self.bar.setValue(1000)
        if result.get("session"):
            self.session_path = result["session"]
            self.open_session.setEnabled(True)
        report = result.get("report")
        if report:
            self.output_path = report["output"]
            self.open_output.setEnabled(True)
            self.imu_metric.setText(f"IMU\n{report['imu_frames']:,} 帧  ·  {report['imu_batches']:,} 接收批次")
            self.lidar_metric.setText(f"雷达\n{report['lidar_packets']:,} 包  ·  {report['lidar_frames']:,} 帧")
            counts = report["packet_status_counts"]
            paired = counts.get("host_bracketed", 0)
            problems = report["lidar_packets"] - paired
            quality = result.get("capture_status", report.get("capture_status", "未知"))
            self.state.setText(f"{'已保存并生成主机时间关联' if result['ok'] else '已保存，关联结果需检查'} · "
                               f"采集质量 {quality}\n可关联 {paired:,} 包；范围外／断流／序号异常 {problems:,} 包；"
                               f"异常雷达记录 {report['invalid_packets']} 条")
            self.append_log(f"关联报告：{self.output_path}/association.json")
            self.append_log(f"实际主机端点重叠 {report['host_overlap_s']:.3f} 秒；精确同步和空间外参未验证。")
        else:
            self.state.setText("任务失败，已有原始文件保留：" + result.get("error", "未知错误"))
            self.append_log(self.state.text())

    def worker_finished(self):
        self.worker.deleteLater()
        self.worker = None
        self.stop_button.setEnabled(False)
        self.form_box.setEnabled(True)
        self.start_button.setEnabled(True)
        self.existing_button.setEnabled(True)
        if self.closing:
            QTimer.singleShot(0, self.close)

    def open_folder(self, path):
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).resolve())))

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.closing = True
            self.stop_capture()
            self.state.setText("正在安全收尾；保存与关联完成后窗口自动关闭。")
            event.ignore()
        else:
            event.accept()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setFont(QFont("Microsoft YaHei UI", 10))
    window = CaptureWindow(load_config(args.config))
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
