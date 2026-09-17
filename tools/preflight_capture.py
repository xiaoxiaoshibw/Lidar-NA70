"""Read-only real PCAP preflight; does not run registration or accept R7."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))
from backend.decoder import scan_pcap
from backend.preflight import diagnostics, evaluate, metadata

if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("pcap",type=Path)
    parser.add_argument("output",type=Path)
    parser.add_argument("--metadata",type=Path)
    args=parser.parse_args()
    if args.output.exists():
        parser.error("输出已存在；请使用新的证据文件名")
    with args.pcap.open('rb') as handle:
        sha=hashlib.file_digest(handle,'sha256').hexdigest()
    scan=scan_pcap(args.pcap)
    d=diagnostics(args.pcap,scan.frames,dict(sha256=sha,duration=scan.duration,valid_packets=scan.valid_packets,invalid_packets=scan.invalid_packets,warnings=scan.warnings))
    m=metadata(json.loads(args.metadata.read_text('utf-8')) if args.metadata else None)
    args.output.write_text(json.dumps(evaluate(d,m),ensure_ascii=False,indent=2),encoding='utf-8')
