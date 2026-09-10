import json
import struct
import time
from uuid import uuid4

from fastapi.testclient import TestClient
from backend.app import create_app
from test_decoder import make_pcap, payload


def wait_ready(client, identifier):
    for _ in range(200):
        meta = client.get(f"/api/captures/{identifier}").json()
        if meta["status"] in ("ready", "error"): return meta
        time.sleep(.01)
    raise AssertionError("indexing did not finish")


def test_upload_binary_pagination_delete_and_restart(tmp_path):
    source = make_pcap(tmp_path / "source.pcap", [payload()] * 101)
    root = tmp_path / "store"
    identifier = str(uuid4())
    with TestClient(create_app(root)) as client:
        response = client.post('/api/captures', params=dict(id=identifier, filename="测试.pcap"), content=source.read_bytes())
        assert response.status_code == 202
        meta = wait_ready(client, identifier)
        assert meta["frame_count"] == 2 and meta["analysis_status"] == "not_analyzed"
        assert len(meta["sha256"]) == 64
        page = client.get(f"/api/captures/{identifier}/frames?offset=1&limit=1").json()
        assert page["total"] == 2 and not page["frames"][0]["complete"]
        assert "offsets" not in page["frames"][0]
        data = client.get(f"/api/captures/{identifier}/frames/0").content
        length, = struct.unpack_from("<I", data)
        header = json.loads(data[4:4 + length])
        assert header["point_count"] == 20000 and header["frame_id"] == "lidar_link"
        assert len(data) - length - 4 == 20000 * 16
        assert client.get(f"/api/captures/{identifier}/frames/-1").status_code == 404
        assert client.get(f"/api/captures/{identifier}/frames?limit=0").status_code == 422
    with TestClient(create_app(root)) as client:
        assert client.get(f"/api/captures/{identifier}").json()["status"] == "ready"
        assert client.delete(f"/api/captures/{identifier}").status_code == 204
        assert not (root / identifier).exists() and source.exists()
        assert client.get(f"/api/captures/{identifier}").status_code == 404


def test_invalid_upload_and_path_id(tmp_path):
    with TestClient(create_app(tmp_path / "store")) as client:
        for data in (b"", b"wrong format" * 10):
            identifier = str(uuid4())
            assert client.post('/api/captures', params=dict(id=identifier, filename="bad.pcap"), content=data).status_code == 202
            assert wait_ready(client, identifier)["status"] == "error"
        assert client.post('/api/captures?id=not-uuid&filename=bad', content=b"x").status_code == 422
