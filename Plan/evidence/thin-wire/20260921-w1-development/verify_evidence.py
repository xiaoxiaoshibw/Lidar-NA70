"""Check artifact hashes and raw export correspondence against the formal decoder."""
from pathlib import Path
import hashlib
import json
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "web"))
from backend import decoder


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            value.update(block)
    return value.hexdigest()


def main():
    run = Path(sys.argv[1]).resolve()
    manifest = json.loads((run / "manifest.json").read_text("utf-8"))
    assert manifest["status"] == "COMPLETED"
    for relative, expected in manifest["source_hashes"].items():
        assert digest(ROOT / relative) == expected, relative
    for relative, expected in manifest["artifacts"].items():
        assert digest(run / relative) == expected, relative
    rows = [json.loads(line) for line in (run / "window_metrics.jsonl").read_text("utf-8").splitlines()]
    inputs = {}
    for entry in manifest["inputs"]:
        path = Path(entry["path"])
        assert digest(path) == entry["sha256"]
        inputs[entry["sha256"]] = path, decoder.scan_pcap(path)
    checked = []
    for row in rows:
        path, scan = inputs[row["input_sha256"]]
        with np.load(run / row["raw_file"], allow_pickle=False) as raw:
            count = len(raw["points"])
            assert count == row["ray_count"]
            refs = (raw["frame_indices"].astype(np.int64) << 32) + raw["point_indices"]
            assert len(np.unique(refs)) == count
            valid = (raw["codes"][:, 0] != 0) & np.isfinite(raw["points"]).all(axis=1)
            np.testing.assert_array_equal(valid, raw["valid_mask"])
            assert int(valid.sum()) == row["valid_return_count"]
            samples = np.unique(np.linspace(0, count - 1, min(96, count), dtype=int)) if count else []
            for frame_index in np.unique(raw["frame_indices"][samples]):
                selected = np.asarray(samples)[raw["frame_indices"][samples] == frame_index]
                frame = scan.frames[int(frame_index)]
                indices = raw["point_indices"][selected]
                expected = decoder.decode_frame(path, frame, manifest["config"]["install_direction"])
                codes = decoder.decode_frame_codes(path, frame)
                np.testing.assert_array_equal(raw["points"][selected], expected[indices])
                np.testing.assert_array_equal(raw["codes"][selected], codes[indices])
                np.testing.assert_array_equal(raw["packet_indices"][selected], frame["packet_start_index"] + indices // 200)
            if row["window_kind"] == "device_time" and count:
                assert np.all(raw["device_times"] > row["requested_start_time_exclusive"])
                assert np.all(raw["device_times"] <= row["end_device_time"])
            assert row["wire_return_status"] == "NOT_EVALUATED"
            assert row["confirmed_target_return_count"] is None
            checked.append({"window_id": row["window_id"], "rays": count,
                            "sampled_decoder_correspondences": len(samples), "status": "PASS"})
    result = {"status": "PASS", "scope": "artifact integrity and raw point correspondence, not wire detection",
              "command": [sys.executable, *sys.argv], "run": str(run),
              "artifact_hashes_checked": len(manifest["artifacts"]), "windows": checked}
    destination = Path(__file__).with_name(f"evidence-verification-{run.name}.json")
    if destination.exists():
        raise FileExistsError(destination)
    destination.write_text(json.dumps(result, indent=2), "utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
