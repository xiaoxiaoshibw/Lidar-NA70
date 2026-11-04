"""Demo session API: session lifecycle, snapshot shape, cloud and debug feeds."""
import json
import struct
import time
from uuid import uuid4

from fastapi.testclient import TestClient
from backend.app import create_app
from test_decoder import make_pcap, payload

from test_api import wait_ready


def wait_done(client, session_id):
    for _ in range(400):
        state = client.get(f"/api/demo/sessions/{session_id}").json()
        if state["status"] in ("done", "error"):
            return state
        time.sleep(.01)
    raise AssertionError("demo session did not finish")


def test_demo_session_lifecycle(tmp_path):
    source = make_pcap(tmp_path / "source.pcap", [payload()] * 205)
    root = tmp_path / "store"
    identifier = str(uuid4())
    with TestClient(create_app(root)) as client:
        client.post('/api/captures', params=dict(id=identifier, filename="demo.pcap"),
                    content=source.read_bytes())
        wait_ready(client, identifier)
        created = client.post("/api/demo/sessions", json={"capture_id": identifier})
        assert created.status_code == 202
        session_id = created.json()["session_id"]
        state = wait_done(client, session_id)
        assert state["status"] == "done", state
        assert state["imu"] is False and state["lidar_online"] is True
        assert state["frame_index"] == 2  # 205 包 → 2 整帧 + 1 不完整尾帧
        assert state["accumulated_frames"] == 3
        assert "goal" in state and isinstance(state["goal"], dict)
        assert set(state["goal"]) >= {"detected", "state"}
        # 云端点接口：v1 帧格式
        cloud = client.get(f"/api/demo/sessions/{session_id}/cloud?mode=accumulated")
        assert cloud.status_code == 200
        buffer = cloud.content
        if buffer:
            length = struct.unpack("<I", buffer[:4])[0]
            meta = json.loads(buffer[4:4 + length])
            assert meta["version"] == 1 and meta["mode"] == "accumulated"
            assert len(buffer) - 4 - length == meta["point_count"] * 16
            if meta["point_count"]:
                provenance = client.get(
                    f"/api/demo/sessions/{session_id}/provenance?voxel_index=0")
                assert provenance.status_code == 200
                provenance_body = provenance.json()
                assert provenance_body["total"] >= provenance_body["returned"] >= 1
                assert all(len(ref) == 2 for ref in provenance_body["source_points"])
        single = client.get(f"/api/demo/sessions/{session_id}/cloud?mode=single")
        assert single.status_code == 200
        assert client.get(f"/api/demo/sessions/{session_id}/debug").status_code == 200
        pipeline = client.get(
            f"/api/demo/sessions/{session_id}/pipeline?source=single").json()
        stage_names = [stage["stage"] for stage in pipeline["stages"]]
        assert stage_names[:3] == ["finite", "range", "z_height"]
        assert set(stage_names) >= {"finite", "range", "z_height", "pole_extract"}
        assert all(stage["trace_kind"] == "execution" for stage in pipeline["stages"])
        assert all(stage["latency_ms"] is None or stage["latency_ms"] >= 0
                   for stage in pipeline["stages"])
        assert all(stage["latency_ms"] is None for stage in pipeline["stages"]
                   if stage["status"] == "skipped")
        debug_cloud = client.get(
            f"/api/demo/sessions/{session_id}/debug/cloud?source=single&stage=fov")
        assert debug_cloud.status_code == 200
        if debug_cloud.content:
            debug_header_length = struct.unpack("<I", debug_cloud.content[:4])[0]
            debug_meta = json.loads(debug_cloud.content[4:4 + debug_header_length])
            assert debug_meta["version"] == 3
            assert debug_meta["classes"] == {
                "rejected": 0, "active": 1, "supporting": 2, "context_raw": 3}
            assert debug_meta["fields"][-1] == "highlight"
            assert len(debug_cloud.content) - 4 - debug_header_length == debug_meta["point_count"] * 24
        assert client.get(f"/api/demo/sessions/{session_id}/logs").json()["total"] > 0
        assert client.get(f"/api/demo/sessions/{session_id}/metrics").json()["frames_observed"] == 3
        config_before = client.get(f"/api/demo/sessions/{session_id}/config").json()
        updated = client.put(f"/api/demo/sessions/{session_id}/config",
                             json={"focus": {"half_size": 7.0}})
        assert updated.status_code == 200
        assert updated.json()["config"]["focus"]["half_size"] == 7.0
        assert updated.json()["version"] != config_before["version"]
        debug_snapshot = client.get(f"/api/demo/sessions/{session_id}/snapshot").json()
        assert set(debug_snapshot) >= {"pipeline", "candidates", "association", "logs", "config_version"}
        deleted = client.delete(f"/api/demo/sessions/{session_id}")
        assert deleted.status_code == 204
        assert client.get(f"/api/demo/sessions/{session_id}").status_code == 404


def test_demo_captures_filtered(tmp_path):
    source = make_pcap(tmp_path / "source.pcap", [payload()] * 105)
    root = tmp_path / "store"
    identifier = str(uuid4())
    with TestClient(create_app(root)) as client:
        client.post('/api/captures', params=dict(id=identifier, filename="9.14_caochangyou.pcap"),
                    content=source.read_bytes())
        client.post('/api/captures', params=dict(id=str(uuid4()), filename="别的数据.pcap"),
                    content=source.read_bytes())
        wait_ready(client, identifier)
        listing = client.get("/api/demo/captures").json()
        names = [entry["filename"] for entry in listing]
        assert "9.14_caochangyou.pcap" in names
        assert "别的数据.pcap" not in names


def test_demo_session_speed_and_window(tmp_path):
    source = make_pcap(tmp_path / "source.pcap", [payload()] * 205)
    root = tmp_path / "store"
    identifier = str(uuid4())
    with TestClient(create_app(root)) as client:
        client.post('/api/captures', params=dict(id=identifier, filename="demo.pcap"),
                    content=source.read_bytes())
        wait_ready(client, identifier)
        created = client.post("/api/demo/sessions", json={
            "capture_id": identifier, "speed": 5,
            "config": {"accumulation": {"window_size": 2}}})
        assert created.status_code == 202
        state = wait_done(client, created.json()["session_id"])
        assert state["window_size"] == 2 and state["speed"] == 5
        bad = client.post("/api/demo/sessions", json={
            "capture_id": identifier, "speed": 100})
        assert bad.status_code == 422


def test_demo_session_rejects_unknown_capture(tmp_path):
    root = tmp_path / "store"
    with TestClient(create_app(root)) as client:
        response = client.post("/api/demo/sessions", json={"capture_id": str(uuid4())})
        assert response.status_code == 404
        bad = client.post("/api/demo/sessions", json={"capture_id": "not-a-uuid"})
        assert bad.status_code == 422


def test_demo_session_config_override_validated(tmp_path):
    source = make_pcap(tmp_path / "source.pcap", [payload()] * 105)
    root = tmp_path / "store"
    identifier = str(uuid4())
    with TestClient(create_app(root)) as client:
        client.post('/api/captures', params=dict(id=identifier, filename="demo.pcap"),
                    content=source.read_bytes())
        wait_ready(client, identifier)
        ok = client.post("/api/demo/sessions", json={
            "capture_id": identifier, "config": {"accumulation": {"window_size": 1}}})
        assert ok.status_code == 202
        state = wait_done(client, ok.json()["session_id"])
        assert state["window_size"] == 1
        client.delete(f"/api/demo/sessions/{ok.json()['session_id']}")
        bad = client.post("/api/demo/sessions", json={
            "capture_id": identifier, "config": {"accumulation": {"window_size": 0}}})
        assert bad.status_code == 422


def test_demo_session_pause_resume_seek(tmp_path):
    source = make_pcap(tmp_path / "source.pcap", [payload()] * 205)
    root = tmp_path / "store"
    identifier = str(uuid4())
    with TestClient(create_app(root)) as client:
        client.post('/api/captures', params=dict(id=identifier, filename="demo.pcap"),
                    content=source.read_bytes())
        wait_ready(client, identifier)
        created = client.post("/api/demo/sessions", json={
            "capture_id": identifier, "speed": 0.25})
        session_id = created.json()["session_id"]
        # 暂停：frame_index 不应推进
        assert client.post(f"/api/demo/sessions/{session_id}/control",
                           json={"action": "pause"}).json()["paused"] is True
        time.sleep(0.6)
        state = client.get(f"/api/demo/sessions/{session_id}").json()
        held = state["frame_index"]
        assert state["paused"] is True
        time.sleep(0.4)
        assert client.get(f"/api/demo/sessions/{session_id}").json()["frame_index"] == held
        # seek 到第 2 帧（205 包共 3 帧）
        client.post(f"/api/demo/sessions/{session_id}/control",
                    json={"action": "seek", "frame": 2})
        done = wait_done(client, session_id)
        assert done["status"] == "done"
        assert done["frame_index"] == 2
        # 非法控制
        assert client.post(f"/api/demo/sessions/{session_id}/control",
                           json={"action": "teleport"}).status_code == 422
        assert client.post(f"/api/demo/sessions/{session_id}/control",
                           json={"action": "seek", "frame": 99}).status_code == 422
