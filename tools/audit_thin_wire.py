"""W1: export unvoxelized NA70 returns, device-time windows and background diagnostics."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))

from backend.thin_wire import DEFAULT_CONFIG, load_config, run_audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pcap", type=Path, required=True, help="Primary classic PCAP")
    parser.add_argument("--background-pcap", type=Path, help="Separate recording of same site without target")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="JSON-compatible YAML configuration")
    parser.add_argument("--output", type=Path, required=True, help="New evidence directory; existing directories are refused")
    parser.add_argument("--end-frame", type=int, action="append", help="Window end frame, repeatable; default last frame")
    parser.add_argument("--background-end-frame", type=int, action="append", help="Independent background end frame; default its last frame")
    args = parser.parse_args()
    try:
        cfg = load_config(args.config)
        result = run_audit(args.pcap, args.output, cfg, args.end_frame,
                           background_pcap=args.background_pcap,
                           background_end_frames=args.background_end_frame,
                           command=[sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])
    except (ValueError, OSError) as error:
        parser.exit(1, f"W1 audit failed: {error}\n")
    print(f"Evidence: {args.output.resolve()}\nTool: {result['status']}; wire returns: {result['wire_return_status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
