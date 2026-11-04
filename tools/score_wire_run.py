"""W4-B reviewed-only scorer CLI.

Reads a run directory (candidate.json and/or compare.jsonl) plus a
wire-truth-v1 JSON list and writes a score artifact next to the run.
Without reviewed truth rows the output is NOT_EVALUATED with null metrics -
never zero-filled.

Usage:
  ./web/.venv/Scripts/python.exe tools/score_wire_run.py \
      --run Plan/evidence/hv-line/20261003-w4a-compare-real-zhong2 \
      --truth tools/scenarios/wire_truth_example_v1.json \
      --output Plan/evidence/hv-line/20261003-w4a-compare-real-zhong2/validation.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))
sys.path.insert(0, str(ROOT))

from na70_core.evidence.scoring import score_run_directory  # noqa: E402


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--truth", required=True, type=Path)
    parser.add_argument("--hit-radius", type=float, default=5.0)
    parser.add_argument("--output", type=Path, default=None,
                        help="default <run>/validation.json")
    args = parser.parse_args(argv)

    result = score_run_directory(args.run, args.truth, args.hit_radius)
    result["command"] = [sys.executable, *sys.argv]
    result["environment"] = {"python": sys.version, "platform": platform.platform()}
    result["truth_sha256"] = sha256_file(args.truth)
    result["run_dir"] = str(args.run)

    output = args.output or (args.run / "validation.json")
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False), "utf-8")
    print(json.dumps({"output": str(output), "status": result["status"],
                      "metrics": result["metrics"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
