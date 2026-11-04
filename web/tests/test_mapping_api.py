"""SYNTHETIC mapping-job API tests.

They exercise persistence, cancellation, progress, chunked map reads and restart
recovery on generated captures.  A real NA70 moving-capture acceptance run is a
separate, manual procedure (see ``Plan/MASTER_开发与验收总计划.md``) and is not
claimed here.
"""
import json
import struct
import time
import zipfile
from functools import lru_cache
from uuid import uuid4

import numpy as np
from fastapi.testclient import TestClient

from backend.app import create_app
from test_decoder import make_pcap


def rich_payload(triples):
    """One 1410-byte NA70 payload carrying 200 distinct points."""
    body = b"".join(struct.pack(">HHHB", distance, pitch, yaw, 77) for distance, pitch, yaw in triples)
    return body + b"\0\0\0\0\0\x01\0\x7b" + struct.pack("<H", 0)


@lru_cache(maxsize=None)
def scene(frames, seed=4, points_per_packet=200):
    """A fixed thick hemispherical shell, identical in every frame.

    Identical frames make scan-to-submap registration a trivial identity, which
    is what an API test needs: the point is the job chain, not the estimator.
    Building 600 payloads is the slow part, so the result is cached.
    """
    rng = np.random.default_rng(seed)
    packets = []
    for _ in range(100):
        triples = [(int(rng.uniform(500, 8000)), int(rng.uniform(0, 1200)), int(rng.uniform(0, 60000)))
                   for _ in range(points_per_packet)]
        packets.append(rich_payload(triples))
    return [packet for _ in range(frames) for packet in packets]


def upload(client, path, frames=6, payloads=None):
    identifier = str(uuid4())
    source = make_pcap(path, payloads if payloads is not None else scene(frames))
    response = client.post('/api/captures', params=dict(id=identifier, filename='synthetic.pcap'),
                           content=source.read_bytes())
    assert response.status_code == 202
    for _ in range(400):
        meta = client.get(f"/api/captures/{identifier}").json()
        if meta["status"] in ("ready", "error"):
            return identifier, meta
        time.sleep(0.01)
    raise AssertionError("indexing did not finish")


def wait_job(client, job_id, timeout=60.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/mapping/jobs/{job_id}").json()
        if job["status"] in ("ready", "error", "cancelled"):
            return job
        time.sleep(0.02)
    raise AssertionError(f"mapping job did not finish: {job['status']}")


def start_job(client, identifier, config=None):
    body = dict(capture_id=identifier)
    if config:
        body["config"] = config
    response = client.post("/api/mapping/jobs", json=body)
    assert response.status_code == 202, response.text
    return response.json()


def test_repair_configuration_and_map_cloud(tmp_path):
    with TestClient(create_app(tmp_path / 'store')) as client:
        identifier, _ = upload(client, tmp_path / 'synthetic.pcap', frames=2)
        for config in ({'voxel_size': 0}, {'method': 'unknown'}, {'submap_frames': -1},
                       {'roi': {'min': [0, 0, 0], 'max': [-1, 1, 1]}},
                       {'initialization_mode': 'stationary_window'}, {'typo': 2}):
            response = client.post('/api/mapping/jobs', json={'capture_id': identifier, 'config': config})
            assert response.status_code == 422, response.text
        job = wait_job(client, start_job(client, identifier)['job_id'])
        assert job['status'] == 'ready', job
        assert job['peak_memory_mb'] is None or job['peak_memory_mb'] >= 1
        response = client.get(f"/api/mapping/jobs/{job['job_id']}/frames/1/cloud")
        assert response.status_code == 200, response.text
        length = struct.unpack('<I', response.content[:4])[0]
        header = json.loads(response.content[4:4+length])
        assert header['coordinate_frame'] == 'map'
        assert len(response.content) == 4 + length + header['point_count'] * 16


def test_persist_failure_never_publishes_ready(tmp_path, monkeypatch):
    from backend.mapping_jobs import MappingJobs
    def fail(*args):
        raise OSError('SYNTHETIC disk failure')
    monkeypatch.setattr(MappingJobs, '_persist', fail)
    with TestClient(create_app(tmp_path / 'store')) as client:
        identifier, _ = upload(client, tmp_path / 'synthetic.pcap', frames=1)
        job = wait_job(client, start_job(client, identifier)['job_id'])
        assert job['status'] == 'error'
        assert 'disk failure' in job['error']
        assert client.post(f"/api/mapping/jobs/{job['job_id']}/export").status_code == 409


def test_cancel_during_persist_cannot_become_ready(tmp_path, monkeypatch):
    from backend.mapping_jobs import MappingJobs
    persist = MappingJobs._persist
    def cancelling(self, job, *args):
        result = persist(self, job, *args)
        self.controls[job['job_id']]['cancel'].set()
        return result
    monkeypatch.setattr(MappingJobs, '_persist', cancelling)
    with TestClient(create_app(tmp_path / 'store')) as client:
        identifier, _ = upload(client, tmp_path / 'synthetic.pcap', frames=1)
        job = wait_job(client, start_job(client, identifier)['job_id'])
        assert job['status'] == 'cancelled'


def test_job_persists_results_and_survives_restart(tmp_path):
    root = tmp_path / "store"
    with TestClient(create_app(root)) as client:
        identifier, meta = upload(client, tmp_path / "synthetic.pcap")
        assert meta["status"] == "ready"
        job = start_job(client, identifier)
        for key in ("job_id", "capture_id", "input_sha256", "code_version", "config_version",
                    "status", "progress", "created_at", "started_at", "finished_at", "frame_count",
                    "accepted_frames", "rejected_frames", "tracking_lost_frames", "keyframe_count",
                    "map_point_count", "map_bounds", "first_frame_index", "last_frame_index",
                    "imu_used", "loop_closure_used", "error"):
            assert key in job, key
        assert job["imu_used"] is False and job["loop_closure_used"] is False
        assert job["input_sha256"] == meta["sha256"]

        finished = wait_job(client, job["job_id"])
        assert finished["status"] == "ready", finished.get("error")
        assert finished["progress"] == 100.0
        assert finished["accepted_frames"] >= 1
        assert finished["frame_count"] == finished["last_frame_index"] + 1
        assert finished["map_point_count"] > 0
        assert finished["map_bounds"]["minimum"] and finished["map_bounds"]["maximum"]

        directory = root / identifier / "mapping" / job["job_id"]
        for name in ("job.json", "config.json", "trajectory.json", "trajectory.csv",
                     "frame_quality.jsonl", "keyframes.json", "map_metadata.json",
                     "map.ply", "map.pcd", "validation.json", "map_chunks/index.json"):
            assert (directory / name).exists(), name

        trajectory = client.get(f"/api/mapping/jobs/{job['job_id']}/trajectory").json()
        assert trajectory["segments"] and trajectory["frames"][0]["state"] == "reference"
        frame = client.get(f"/api/mapping/jobs/{job['job_id']}/frames/0").json()
        assert frame["index"] == 0 and frame["state"] == "reference"
        assert client.get(f"/api/mapping/jobs/{job['job_id']}/frames/9999").status_code == 404
        map_meta = client.get(f"/api/mapping/jobs/{job['job_id']}/map").json()
        assert map_meta["voxel_count"] > 0 and map_meta["chunk_count"] >= 1
        assert map_meta["imu_used"] is False and map_meta["loop_closure_used"] is False
        assert map_meta["input_sha256"] == meta["sha256"]

        validation = json.loads((directory / "validation.json").read_text("utf-8"))
        assert validation["claims"]["engineering_chain"] == "PASSED"
        assert validation["claims"]["real_capture_ghosting_acceptance"] == "NOT_EVALUATED"
        assert validation["claims"]["jetson_acceptance"] == "NOT_RUN"
        assert validation["metrics"]["accepted_frames"] == finished["accepted_frames"]
        assert validation["environment"]["numpy"]

        legacy = client.get(f"/api/captures/{identifier}/registration").json()
        assert legacy["status"] == "ready" and legacy["result"]["imu"] is False
        assert legacy["result"]["frame_count"] == finished["frame_count"]

    # A fresh process must still see the finished job, its trajectory and its map.
    with TestClient(create_app(root)) as client:
        restored = client.get(f"/api/mapping/jobs/{job['job_id']}").json()
        assert restored["status"] == "ready" and restored["input_sha256"] == meta["sha256"]
        assert client.get(f"/api/mapping/jobs/{job['job_id']}/trajectory").status_code == 200
        assert client.get(f"/api/mapping/jobs/{job['job_id']}/map").status_code == 200
        listed = client.get(f"/api/captures/{identifier}/mapping/jobs").json()["jobs"]
        assert [entry["job_id"] for entry in listed] == [job["job_id"]]


def test_interrupted_job_is_reported_after_restart(tmp_path):
    root = tmp_path / "store"
    with TestClient(create_app(root)) as client:
        identifier, _ = upload(client, tmp_path / "synthetic.pcap")
    directory = root / identifier / "mapping" / "00000000-0000-0000-0000-000000000001"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "job.json").write_text(json.dumps(dict(
        job_id="00000000-0000-0000-0000-000000000001", capture_id=identifier, status="running",
        progress=42.0, created_at=1.0, input_sha256="x")), "utf-8")
    (root / identifier / "mapping" / "index.json").write_text(json.dumps(dict(
        jobs=[dict(job_id="00000000-0000-0000-0000-000000000001", status="running", created_at=1.0)]),
    ), "utf-8")
    with TestClient(create_app(root)) as client:
        job = client.get("/api/mapping/jobs/00000000-0000-0000-0000-000000000001").json()
        assert job["status"] == "error" and "中断" in job["error"]


def test_progress_is_reported_while_running(tmp_path):
    with TestClient(create_app(tmp_path / "store")) as client:
        identifier, _ = upload(client, tmp_path / "synthetic.pcap", frames=24)
        job = start_job(client, identifier)
        seen = {job["progress"]}
        deadline = time.time() + 60
        while time.time() < deadline:
            current = client.get(f"/api/mapping/jobs/{job['job_id']}").json()
            seen.add(current["progress"])
            if current["status"] in ("ready", "error", "cancelled"):
                break
            time.sleep(0.005)
        current = client.get(f"/api/mapping/jobs/{job['job_id']}").json()
        assert current["status"] == "ready", current.get("error")
        assert current["progress"] == 100.0
        assert current["started_at"] and current["finished_at"] >= current["started_at"]
        assert current["elapsed_seconds"] is not None
        assert current["peak_memory_mb"] is not None
        assert max(seen) <= 100.0 and min(seen) >= 0.0


def test_job_can_be_cancelled(tmp_path):
    with TestClient(create_app(tmp_path / "store")) as client:
        identifier, _ = upload(client, tmp_path / "synthetic.pcap", frames=60)
        job = start_job(client, identifier)
        assert client.post(f"/api/mapping/jobs/{job['job_id']}/cancel").status_code == 200
        final = wait_job(client, job["job_id"])
        assert final["status"] == "cancelled", final
        assert final["finished_at"] is not None
        validation = json.loads((tmp_path / "store" / identifier / "mapping" / job["job_id"]
                                 / "validation.json").read_text("utf-8"))
        assert validation["claims"]["engineering_chain"] == "CANCELLED"
        # A cancelled job has no usable map, so exporting must be refused.
        assert client.post(f"/api/mapping/jobs/{job['job_id']}/export").status_code == 409
        # And the record can start a new job afterwards.
        assert client.post("/api/mapping/jobs", json=dict(capture_id=identifier)).status_code == 202


def test_second_job_is_refused_while_one_runs(tmp_path):
    with TestClient(create_app(tmp_path / "store")) as client:
        identifier, _ = upload(client, tmp_path / "synthetic.pcap", frames=24)
        job = start_job(client, identifier)
        assert client.post("/api/mapping/jobs", json=dict(capture_id=identifier)).status_code == 409
        wait_job(client, job["job_id"])
        assert client.post("/api/mapping/jobs", json=dict(capture_id=identifier)).status_code == 202


def test_map_chunks_are_addressable_and_bounded(tmp_path):
    with TestClient(create_app(tmp_path / "store")) as client:
        identifier, _ = upload(client, tmp_path / "synthetic.pcap")
        job = start_job(client, identifier)
        finished = wait_job(client, job["job_id"])
        assert finished["status"] == "ready", finished.get("error")
        endpoint = f"/api/mapping/jobs/{job['job_id']}/map/chunks"

        everything = client.get(endpoint).json()
        assert everything["returned_chunks"] == everything["chunk_count"] >= 1
        assert everything["returned_voxels"] == finished["metrics"]["map_voxel_count"]
        assert everything["fields"][0:3] == ["x", "y", "z"]
        first = everything["chunks"][0]
        assert len(first["voxels"][0]) == 8 and first["voxel_count"] <= first["total_voxels"]

        one = client.get(endpoint, params={"ids": first["id"]}).json()
        assert one["returned_chunks"] == 1 and one["chunks"][0]["id"] == first["id"]

        assert client.get(endpoint, params={"ids": "nope_1_2"}).json()["returned_chunks"] == 0

        tiny = client.get(endpoint, params={"limit": 25}).json()
        assert tiny["returned_voxels"] <= 25 and tiny["truncated"] is True

        # A view query must not return chunks that cannot intersect the box.
        boxed = client.get(endpoint, params={"min_x": 1e6, "min_y": 1e6, "min_z": 1e6}).json()
        assert boxed["returned_chunks"] == 0

    # Chunks survive a restart because they are files, not memory.
    with TestClient(create_app(tmp_path / "store")) as client:
        again = client.get(endpoint).json()
        assert again["returned_voxels"] == everything["returned_voxels"]


def test_export_bundle_contains_the_evidence(tmp_path):
    with TestClient(create_app(tmp_path / "store")) as client:
        identifier, _ = upload(client, tmp_path / "synthetic.pcap")
        job = start_job(client, identifier)
        wait_job(client, job["job_id"])
        manifest = client.post(f"/api/mapping/jobs/{job['job_id']}/export").json()
        assert manifest["bundle"] == "mapping-evidence.zip" and manifest["bundle_bytes"] > 0
        assert manifest["input_sha256"] and manifest["config_version"]
        download = client.get(f"/api/mapping/jobs/{job['job_id']}/export/{manifest['bundle']}")
        assert download.status_code == 200 and download.content[:2] == b"PK"
        archive = tmp_path / "bundle.zip"
        archive.write_bytes(download.content)
        with zipfile.ZipFile(archive) as handle:
            names = set(handle.namelist())
        for required in ("job.json", "trajectory.csv", "frame_quality.jsonl", "map.ply",
                         "map.pcd", "validation.json", "map_metadata.json"):
            assert required in names, required
        assert client.get(f"/api/mapping/jobs/{job['job_id']}/export/nope.txt").status_code == 404


def test_unknown_job_and_bad_capture_are_rejected(tmp_path):
    with TestClient(create_app(tmp_path / "store")) as client:
        missing = "00000000-0000-0000-0000-0000000000ff"
        assert client.get(f"/api/mapping/jobs/{missing}").status_code == 404
        assert client.post(f"/api/mapping/jobs/{missing}/cancel").status_code == 404
        assert client.post("/api/mapping/jobs", json=dict(capture_id=str(uuid4()))).status_code == 404
        assert client.get("/api/mapping/jobs").json()["jobs"] == []


def test_config_overrides_are_recorded_with_the_job(tmp_path):
    with TestClient(create_app(tmp_path / "store")) as client:
        identifier, _ = upload(client, tmp_path / "synthetic.pcap")
        job = start_job(client, identifier, config={"voxel_size": 0.5, "min_points": 12})
        finished = wait_job(client, job["job_id"])
        assert finished["status"] == "ready", finished.get("error")
        stored = json.loads((tmp_path / "store" / identifier / "mapping" / job["job_id"]
                             / "config.json").read_text("utf-8"))
        assert stored["voxel_size"] == 0.5 and stored["min_points"] == 12
        assert stored["max_range"] == 80.0           # untouched keys keep the default
        assert finished["config_version"] != start_config_version()
        validation = json.loads((tmp_path / "store" / identifier / "mapping" / job["job_id"]
                                 / "validation.json").read_text("utf-8"))
        assert validation["config"]["voxel_size"] == 0.5


def test_fixed_sensor_scene_requires_a_declaration(tmp_path):
    with TestClient(create_app(tmp_path / "store")) as client:
        identifier, _ = upload(client, tmp_path / "synthetic.pcap", frames=2)
        base = {"pose_source": "fixed_sensor", "stationary_start_frame": 0,
                "stationary_end_frame": 1}
        for declaration in (None, " "):
            config = dict(base)
            if declaration is not None:
                config["stationary_declaration"] = declaration
            response = client.post("/api/mapping/jobs", json={
                "capture_id": identifier, "config": config})
            assert response.status_code == 422, response.text


def test_fixed_sensor_scene_persists_and_survives_restart(tmp_path):
    root = tmp_path / "store"
    with TestClient(create_app(root)) as client:
        identifier, meta = upload(client, tmp_path / "synthetic.pcap", frames=3)
        assert meta["status"] == "ready"
        job = start_job(client, identifier, config={
            "pose_source": "fixed_sensor",
            "stationary_start_frame": 0,
            "stationary_end_frame": 2,
            "stationary_declaration": "SYNTHETIC fixed sensor",
        })
        finished = wait_job(client, job["job_id"])
        assert finished["status"] == "ready", finished.get("error")
        assert finished["stage"] == "fixed_sensor_static_scene"

        directory = root / identifier / "mapping" / job["job_id"]
        validation = json.loads((directory / "validation.json").read_text("utf-8"))
        assert validation["stage"] == "fixed_sensor_static_scene"
        assert validation["pose_source"] == "fixed_sensor"
        assert validation["claims"]["real_capture_ghosting_acceptance"] == "NOT_EVALUATED"
        assert validation["metrics"]["registered_frames"] == 0
        ply = (directory / "map.ply").read_bytes()
        assert ply.startswith(b"ply\n") and len(ply) > len(b"ply\nend_header\n")
        before_restart = client.get(
            f"/api/mapping/jobs/{job['job_id']}/map/chunks").json()
        assert before_restart["returned_voxels"] > 0

    with TestClient(create_app(root)) as client:
        restored = client.get(f"/api/mapping/jobs/{job['job_id']}").json()
        assert restored["status"] == "ready"
        after_restart = client.get(
            f"/api/mapping/jobs/{job['job_id']}/map/chunks").json()
        assert after_restart == before_restart


def test_fixed_sensor_skips_timestamp_anomaly_and_rejects_out_of_range_interval(tmp_path):
    # SYNTHETIC: one bad device timestamp between two otherwise valid frames.
    packets = list(scene(3))
    packets[110] = packets[110][:1400] + b"\0" * 6 + b"\x03\xe8" + b"\0\0"
    with TestClient(create_app(tmp_path / "store")) as client:
        identifier, _ = upload(client, tmp_path / "synthetic-anomaly.pcap", payloads=packets)
        config = {"pose_source": "fixed_sensor", "stationary_start_frame": 0,
                  "stationary_end_frame": 2,
                  "stationary_declaration": "SYNTHETIC fixed sensor"}
        job = wait_job(client, start_job(client, identifier, config=config)["job_id"])
        assert job["status"] == "ready", job.get("error")
        trajectory = client.get(f"/api/mapping/jobs/{job['job_id']}/trajectory").json()
        assert trajectory["frames"][0]["state"] == "fixed_observation"
        assert trajectory["frames"][1]["state"] == "timestamp_invalid"
        assert trajectory["frames"][2]["state"] == "fixed_observation"
        assert job["metrics"]["fixed_observation_frames"] == 2

        out_of_range = dict(config, stationary_end_frame=3)
        response = client.post("/api/mapping/jobs", json={
            "capture_id": identifier, "config": out_of_range})
        assert response.status_code == 422, response.text


def start_config_version():
    from backend.mapping import configuration, configuration_version
    return configuration_version(configuration())
