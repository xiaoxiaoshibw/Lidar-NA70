"""持续双流采集 CLI（A1）：一次会话同时落 IMU 原始流与雷达原始 PCAP。

用法（Jetson 本机）：

    python3 tools/na70_capture_session.py --duration 60 \\
        --imu-port /dev/serial/by-id/usb-WCH.CN_USB_Single_Serial_0003-if00 \\
        --lidar-iface enP8p1s0 --root /home/jetson/na70-sessions --task B1

返回码：0 = ok，3 = degraded（数据可用但有问题），4 = failed（本次不可用），
2 = 会话创建/参数错误。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for candidate in (ROOT / "packages", ROOT):
    if candidate.is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from na70_core.capture import CaptureRunner, CaptureSession, CaptureSessionError  # noqa: E402

DEFAULT_BY_ID = "/dev/serial/by-id/usb-WCH.CN_USB_Single_Serial_0003-if00"
EXIT_CODES = {"ok": 0, "degraded": 3, "failed": 4}


def default_imu_port():
    path = Path(DEFAULT_BY_ID)
    return str(path) if path.exists() else "/dev/ttyACM0"


def status_line(status, remaining):
    parts = [f"{remaining:5.1f}s 待结束"]
    imu = status.get("imu")
    if imu:
        parts.append(f"IMU {imu['state']:<12} {imu['hz']:6.1f} Hz 帧={imu['frames']:<7} "
                     f"校验错={imu['checksum_errors']} 断号={imu['sequence_breaks']}")
    lidar = status.get("lidar")
    if lidar:
        parts.append(f"雷达 {lidar['state']:<6} {lidar['pcap_bytes'] / 1024 ** 2:7.2f} MiB")
    return " | ".join(parts)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(Path.home() / "na70-sessions"),
                        help="会话根目录（每个会话一个子目录，不覆盖）")
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--task", default=None, help="采集任务标签，例如 B1")
    parser.add_argument("--note", action="append", default=[], help="可重复的现场备注")
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--imu-port", default=None)
    parser.add_argument("--imu-baud", type=int, default=460800)
    parser.add_argument("--no-imu", action="store_true")
    parser.add_argument("--lidar-iface", default="enP8p1s0")
    parser.add_argument("--lidar-port", type=int, default=2368)
    parser.add_argument("--no-lidar", action="store_true")
    parser.add_argument("--tcpdump", default="tcpdump")
    parser.add_argument("--run-as", default=None, help="以 root 抓包时降权写入的目标用户")
    parser.add_argument("--min-free-gb", type=float, default=2.0)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    imu_port = args.imu_port or default_imu_port()
    operator = {"notes": args.note, "imu_port_requested": imu_port,
                "lidar_iface": args.lidar_iface, "operator_supplied": False}
    try:
        session = CaptureSession(args.root, session_id=args.session_id, operator=operator,
                                 task=args.task, min_free_bytes=int(args.min_free_gb * 1024 ** 3))
    except CaptureSessionError as exc:
        print(f"会话创建失败：{exc}", file=sys.stderr)
        return 2

    runner = CaptureRunner(
        session,
        imu=None if args.no_imu else {"port": imu_port, "baud": args.imu_baud},
        lidar=None if args.no_lidar else {"iface": args.lidar_iface, "port": args.lidar_port,
                                          "tcpdump": args.tcpdump, "run_as": args.run_as},
    )
    try:
        runner.start()
    except Exception as exc:  # noqa: BLE001
        session.event("capture_start_failed", error=str(exc))
        print(f"采集启动失败：{exc}", file=sys.stderr)
        try:
            runner._safe_stop()  # noqa: SLF001 - 启动失败必须回收已起进程
        finally:
            session.finalize(status="failed", quality={"status": "failed", "error": str(exc)},
                             devices={}, windows={})
        return 4

    def tick(status, remaining):
        if not args.quiet:
            print(status_line(status, remaining), flush=True)

    print(f"会话：{session.path}", flush=True)
    manifest = runner.run(args.duration, on_tick=tick)
    quality = json.loads((session.path / "quality.json").read_text("utf-8"))
    summary = {
        "session": str(session.path),
        "session_id": session.session_id,
        "status": manifest["status"],
        "duration_s": quality.get("duration_s"),
        "overlap_s": quality.get("overlap_s"),
        "time_level": quality.get("time_level"),
        "failed_checks": [c for c in quality.get("checks", []) if not c["ok"]],
        "files": manifest["files"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return EXIT_CODES.get(manifest["status"], 4)


if __name__ == "__main__":
    raise SystemExit(main())