"""Run the production fixed-point turntable mapper on one or more NA70 PCAPs."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))

from backend.turntable import configuration, run_turntable_job  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pcap", action="append", required=True)
    parser.add_argument("--angle-log", required=True)
    parser.add_argument("--calibration", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--config")
    args = parser.parse_args()
    cfg = configuration(Path(args.config)) if args.config else configuration()
    command = " ".join(sys.argv)
    result = run_turntable_job([Path(value) for value in args.pcap], Path(args.angle_log),
                               Path(args.calibration), Path(args.output), cfg, command)
    print(result["job"]["output_directory"])


if __name__ == "__main__":
    main()
