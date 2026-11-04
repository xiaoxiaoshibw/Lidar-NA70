"""Check upstream FAST-LIO initialization and SIGTERM shutdown without sensors."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    log_path = args.output / "startup.log"
    command = [str(args.workspace / "install/fast_lio/lib/fast_lio/fastlio_mapping"), "--ros-args",
               "--params-file", str(args.workspace / "src/fast_lio/config/avia.yaml")]
    with log_path.open("w") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        ready = False
        deadline = time.monotonic() + 15
        while process.poll() is None and time.monotonic() < deadline:
            if "Node init finished." in log_path.read_text():
                ready = True
                break
            time.sleep(.1)
        graceful = True
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                graceful = False
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
    report = dict(environment="WSL2_HUMBLE_X86_64", command=command,
                  initialized=ready, graceful_shutdown=graceful, exit_code=process.returncode,
                  status="STARTUP_SMOKE_PASSED" if ready and graceful and process.returncode == 0 else "FAILED",
                  na70_validated=False, slam_validated=False)
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    return 0 if report["status"] == "STARTUP_SMOKE_PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
