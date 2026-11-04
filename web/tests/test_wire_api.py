"""SYNTHETIC PCAP fixtures exercise the W1 WebUI contract, not wire detection."""
import json
import hashlib
import threading
import time
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.thin_wire import load_config
from test_api import wait_ready
from test_decoder import make_pcap
from test_thin_wire import timed_payload


def upload(client, tmp_path, *, name="wire.pcap", distances=(1250, 12500, 0)):
    source = make_pcap(tmp_path / name, [
        timed_payload(10 + i * .001, distances=distances) for i in range(3)
    ])
    capture_id = str(uuid4())
    response = client.post("/api/captures", params={"id": capture_id, "filename": name},
                           content=source.read_bytes())
    assert response.status_code == 202
    assert wait_ready(client, capture_id)["status"] == "ready"
    return capture_id


def wait_wire(client, run_id):
    for _ in range(300):
        response = client.get(f"/api/wire/runs/{run_id}")
        assert response.status_code == 200
        state = response.json()
        if state["status"] in ("ready", "error"):
            return state
        time.sleep(.01)
    raise AssertionError("W1 run did not finish")


def test_w1_run_windows_timeline_cloud_and_restart(tmp_path):
    root = tmp_path / "store"
    with TestClient(create_app(root)) as client:
        capture_id = upload(client, tmp_path)
        capabilities = client.get("/api/wire/capabilities").json()
        assert capabilities["modes"][0]["id"] == "w1_raw"
        assert capabilities["modes"][0]["evaluation_status"] == "NOT_EVALUATED"
        assert all(not mode["available"] for mode in capabilities["future_modes"])
        assert capabilities["default_config"] == load_config()
        assert capabilities["config_schema"]["properties"]["window_seconds"]["items"]["maximum"] == 5

        created = client.post("/api/wire/runs", json={
            "capture_id": capture_id, "end_frame": 0, "render": False,
        })
        assert created.status_code == 202, created.text
        run_id = created.json()["run_id"]
        state = wait_wire(client, run_id)
        assert state["status"] == "ready", state
        assert state["wire_return_status"] == "NOT_EVALUATED"
        assert state["window_count"] == 5
        assert len(state["windows"]) == 5
        assert "target_roi_missing" in state["evidence_gaps"]
        assert state["request"]["capture_sha256"]
        assert len(client.get("/api/wire/runs", params={"capture_id": capture_id}).json()) == 1

        window_id = "primary-f000000-single"
        detail = client.get(f"/api/wire/runs/{run_id}/windows/{window_id}")
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert body["window"]["ray_count"] == 600
        assert body["window"]["direction_known_count"] + body["window"]["direction_unknown_count"] == 600
        assert body["window"]["wire_return_status"] == "NOT_EVALUATED"
        assert len(body["packets"]) == 3
        assert [packet["index"] for packet in body["packets"]] == [0, 1, 2]
        assert sum(packet["valid_mask"] for packet in body["packets"]) == body["window"]["valid_return_count"]

        cloud = client.get(f"/api/wire/runs/{run_id}/windows/{window_id}/cloud?limit=21")
        assert cloud.status_code == 200, cloud.text
        cloud_body = cloud.json()
        assert cloud_body["sampled"] and len(cloud_body["points"]) == 21
        assert cloud_body["total_valid_points"] == body["window"]["valid_return_count"]
        assert cloud_body["capture_id"] == capture_id
        assert cloud_body["input_role"] == "primary"
        assert cloud_body["coordinate_frame"] == "lidar_link"
        for point in cloud_body["points"]:
            assert point["frame_index"] == 0
            assert 0 <= point["packet_index"] <= 2
            assert 0 <= point["point_index"] < 600
            assert point["device_time"] is not None
            assert point["target_roi"] is False
        empty = client.get(f"/api/wire/runs/{run_id}/windows/{window_id}/cloud?subset=target_roi")
        assert empty.status_code == 200
        assert empty.json()["total_selected_points"] == 0
        assert empty.json()["points"] == []
        assert client.get(f"/api/wire/runs/{run_id}/windows/missing").status_code == 404
        assert client.get(f"/api/wire/runs/{run_id}/windows/{window_id}/cloud?limit=10001").status_code == 422

    with TestClient(create_app(root)) as client:
        restored = client.get(f"/api/wire/runs/{run_id}").json()
        assert restored["status"] == "ready" and restored["window_count"] == 5
        assert client.get(f"/api/wire/runs/{run_id}/windows/{window_id}").status_code == 200


def test_run_config_is_merged_hashed_and_restored(tmp_path):
    root = tmp_path / "store"
    supplied = {"window_seconds": [.25], "include_single_frame": False,
                "acquisition": {"capture_role": "target_present", "notes": "W1 diagnostic only"}}
    with TestClient(create_app(root)) as client:
        capture_id = upload(client, tmp_path)
        created = client.post("/api/wire/runs", json={
            "capture_id": capture_id, "end_frame": 0, "render": False, "config": supplied,
        })
        assert created.status_code == 202, created.text
        run_id = created.json()["run_id"]
        ready = wait_wire(client, run_id)
        assert ready["status"] == "ready", ready
        assert ready["window_count"] == 1
        assert ready["wire_return_status"] == "NOT_EVALUATED"
        config = ready["request"]["config"]
        assert config["window_seconds"] == [.25]
        assert config["include_single_frame"] is False
        assert config["acquisition"]["capture_role"] == "target_present"
        assert config["acquisition"]["data_label"] == "REAL_PCAP_DIAGNOSTIC"
        assert config["max_window_points"] == load_config()["max_window_points"]
        digest = hashlib.sha256(json.dumps(config, sort_keys=True, allow_nan=False).encode()).hexdigest()
        assert ready["config_sha256"] == ready["request"]["config_sha256"] == digest
        persisted = json.loads((root / capture_id / "wire" / run_id / "manifest.json").read_text("utf-8"))
        assert persisted["config"] == config
        assert persisted["config_sha256"] == digest
        assert ready["windows"][0]["window_id"] == "primary-f000000-0.25s"

    with TestClient(create_app(root)) as client:
        restored = client.get(f"/api/wire/runs/{run_id}").json()
        assert restored["status"] == "ready"
        assert restored["request"]["config"] == config
        assert restored["config_sha256"] == digest


def test_run_config_rejects_invalid_fields_and_web_budgets(tmp_path):
    with TestClient(create_app(tmp_path / "store")) as client:
        capture_id = upload(client, tmp_path)
        cases = (
            (None, "config"),
            ({"unknown": 1}, "Unknown"),
            ({"acquisition": {"unknown": 1}}, "acquisition"),
            ({"acquisition": {"notes": ["not text"]}}, "notes"),
            ({"window_seconds": [0]}, "window_seconds"),
            ({"window_seconds": [6]}, "window_seconds"),
            ({"window_seconds": [i / 10 for i in range(1, 10)]}, "window_seconds"),
            ({"max_window_points": 2_000_001}, "max_window_points"),
            ({"max_windows": 65}, "max_windows"),
            ({"render_max_points": 60_001}, "render_max_points"),
            ({"render_dpi": 151}, "render_dpi"),
        )
        for config, expected in cases:
            response = client.post("/api/wire/runs", json={
                "capture_id": capture_id, "render": False, "config": config,
            })
            assert response.status_code == 422, (config, response.text)
            assert expected in response.json()["detail"], (config, response.text)
        assert client.get("/api/wire/runs").json() == []


def test_invalid_inputs_and_distinct_background(tmp_path):
    with TestClient(create_app(tmp_path / "store")) as client:
        capture_id = upload(client, tmp_path, name="primary.pcap")
        background_id = upload(client, tmp_path, name="background.pcap", distances=(2500, 0))
        invalid = (
            ({"capture_id": "not-a-uuid"}, 422),
            ({"capture_id": capture_id, "extra": True}, 422),
            ({"capture_id": capture_id, "render": 1}, 422),
            ({"capture_id": capture_id, "end_frame": True}, 422),
            ({"capture_id": capture_id, "end_frame": 1}, 422),
            ({"capture_id": capture_id, "background_capture_id": capture_id}, 422),
            ({"capture_id": capture_id, "background_end_frame": 0}, 422),
        )
        for payload, expected in invalid:
            response = client.post("/api/wire/runs", json=payload)
            assert response.status_code == expected, (payload, response.text)
        created = client.post("/api/wire/runs", json={
            "capture_id": capture_id, "background_capture_id": background_id,
            "end_frame": 0, "background_end_frame": 0, "render": False,
        })
        assert created.status_code == 202, created.text
        run_id = created.json()["run_id"]
        state = wait_wire(client, run_id)
        assert state["status"] == "ready", state
        assert state["window_count"] == 10
        bg = client.get(f"/api/wire/runs/{run_id}/windows/background-f000000-single/cloud?limit=7").json()
        assert bg["capture_id"] == background_id and bg["input_role"] == "background"


def test_bounded_worker_capture_delete_guard_and_interrupted_restore(tmp_path, monkeypatch):
    from backend import wire_api

    started = threading.Event()
    release = threading.Event()
    original = wire_api.run_audit

    def held_audit(*args, **kwargs):
        started.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(wire_api, "run_audit", held_audit)
    root = tmp_path / "store"
    with TestClient(create_app(root)) as client:
        capture_id = upload(client, tmp_path)
        first = client.post("/api/wire/runs", json={"capture_id": capture_id, "render": False})
        assert first.status_code == 202
        assert started.wait(5)
        second = client.post("/api/wire/runs", json={"capture_id": capture_id, "render": False})
        assert second.status_code == 202
        assert client.post("/api/wire/runs", json={"capture_id": capture_id, "render": False}).status_code == 429
        assert client.delete(f"/api/captures/{capture_id}").status_code == 409
        release.set()
        first_id, second_id = first.json()["run_id"], second.json()["run_id"]
        assert wait_wire(client, first_id)["status"] == "ready"
        assert wait_wire(client, second_id)["status"] == "ready"

    # A process interrupted while queued/running has no trustworthy result.
    state_path = root / capture_id / "wire" / first_id / "state.json"
    state = json.loads(state_path.read_text("utf-8"))
    state["status"] = "running"
    state_path.write_text(json.dumps(state), "utf-8")
    with TestClient(create_app(root)) as client:
        recovered = client.get(f"/api/wire/runs/{first_id}").json()
        assert recovered["status"] == "error"
        assert recovered["wire_return_status"] == "NOT_EVALUATED"
        assert "重启" in recovered["error"]
        assert client.get(f"/api/wire/runs/{first_id}/windows/primary-f000000-single").status_code == 409
        assert client.get(f"/api/wire/runs/{second_id}").json()["status"] == "ready"


def test_cloud_rejects_modified_raw_artifact(tmp_path):
    root = tmp_path / "store"
    with TestClient(create_app(root)) as client:
        capture_id = upload(client, tmp_path)
        created = client.post("/api/wire/runs", json={"capture_id": capture_id, "render": False})
        run_id = created.json()["run_id"]
        assert wait_wire(client, run_id)["status"] == "ready"
        window_id = "primary-f000000-single"
        raw_path = root / capture_id / "wire" / run_id / "evidence" / "raw_windows" / f"{window_id}.npz"
        with raw_path.open("ab") as output:
            output.write(b"modified")
        response = client.get(f"/api/wire/runs/{run_id}/windows/{window_id}/cloud")
        assert response.status_code == 409
