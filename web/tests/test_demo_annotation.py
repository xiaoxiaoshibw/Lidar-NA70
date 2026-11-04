"""Annotation API: label validation, storage binding, on-demand annotate cloud."""
import struct
import time
from uuid import uuid4

from fastapi.testclient import TestClient
from backend.app import create_app
from test_decoder import make_pcap, payload

from test_api import wait_ready


def upload(client, tmp_path, packets=205):
    source = make_pcap(tmp_path / f"source-{uuid4().hex[:6]}.pcap", [payload()] * packets)
    identifier = str(uuid4())
    client.post('/api/captures', params=dict(id=identifier, filename="9.14_caochangyou.pcap"),
                content=source.read_bytes())
    wait_ready(client, identifier)
    return identifier


def test_annotation_context_allows_raw_voxel_zero(tmp_path):
    with TestClient(create_app(tmp_path / "store")) as client:
        identifier = upload(client, tmp_path)
        base = {"capture_id": identifier, "type": "goal", "frame_range": [0, 2],
                "box": {"center": [93.5, -20.0, 1.3], "size": [0.4, 7.0, 2.3], "yaw": 0.1},
                "labeler": "测试员", "strokes": [{"points": [[92.0, -20.0], [95.0, -20.0]],
                                                  "brush_radius": 1.0}]}
        ok = client.post("/api/demo/annotations", json={
            **base, "annotation_context": {
                "frame_index": 2, "device_time": 1.2, "accumulated_frames": 3,
                "window_size": 3, "voxel_size": 0, "range": [0, 2000],
                "source": "demo_accumulated_cloud"}})
        assert ok.status_code == 201, ok.text
        label = client.get(f"/api/demo/annotations?capture_id={identifier}").json()[0]["labels"][0]
        assert label["annotation_context"]["voxel_size"] == 0
        assert label["strokes"][0]["brush_radius"] == 1.0
        bad = client.post("/api/demo/annotations", json={
            **base, "annotation_context": {
                "frame_index": 2, "device_time": 1.2, "accumulated_frames": 3,
                "window_size": 3, "voxel_size": -1, "range": [0, 2000],
                "source": "demo_accumulated_cloud"}})
        assert bad.status_code == 422


def test_strokes_only_goal_label_and_delete(tmp_path):
    with TestClient(create_app(tmp_path / "store")) as client:
        identifier = upload(client, tmp_path)
        base = {"capture_id": identifier, "type": "goal", "frame_range": [0, 2],
                "labeler": "测试员",
                "annotation_context": {
                    "frame_index": 2, "device_time": 1.2, "accumulated_frames": 3,
                    "window_size": 3, "voxel_size": 0, "range": [0, 2000],
                    "source": "demo_accumulated_cloud"}}
        # 无 box、无 points：纯画笔区域真值必须可保存。
        ok = client.post("/api/demo/annotations", json={**base, "strokes": [
            {"points": [[92.0, -20.0], [95.0, -20.0]], "brush_radius": 1.0}]})
        assert ok.status_code == 201, ok.text
        label_id = ok.json()["label_id"]
        label = client.get(f"/api/demo/annotations?capture_id={identifier}").json()[0]["labels"][-1]
        assert label["strokes"][0]["points"] == [[92.0, -20.0], [95.0, -20.0]]
        assert label["label_id"] == label_id and "box" not in label
        # 非法画笔形状全部 422
        for bad in [{"strokes": []}, {"strokes": [{"points": []}]},
                    {"strokes": [{"points": [[0, 0]], "brush_radius": 0}]},
                    {"strokes": [{"points": [["x", 0]]}]}]:
            r = client.post("/api/demo/annotations", json={**base, **bad})
            assert r.status_code == 422, bad
        # 删除：按 label_id 删除后总数减一，重复删除 404。
        total = ok.json()["labels_total"]
        deleted = client.delete(f"/api/demo/annotations?capture_id={identifier}&label_id={label_id}")
        assert deleted.status_code == 200 and deleted.json()["labels_total"] == total - 1
        again = client.delete(f"/api/demo/annotations?capture_id={identifier}&label_id={label_id}")
        assert again.status_code == 404


def test_window_v2_api_roundtrip_checks_capture_time_and_refs(tmp_path):
    from test_goal_window_annotation import window_label
    with TestClient(create_app(tmp_path / 'store')) as client:
        identifier = upload(client, tmp_path)
        meta = client.get(f'/api/captures/{identifier}').json()
        frames = client.get(f'/api/captures/{identifier}/frames').json()['frames']
        label = window_label()
        label.update(capture_id=identifier, capture_sha256=meta['sha256'],
                     device_time_range=[frames[0]['device_start_time'], frames[1]['device_time']])
        label['duration_seconds'] = label['device_time_range'][1]-label['device_time_range'][0]
        saved = client.post('/api/demo/annotations', json=label)
        assert saved.status_code == 201, saved.text
        read = client.get(f'/api/demo/annotations?capture_id={identifier}').json()[0]
        assert read['version'] == 2
        assert read['labels'][0]['components'] == label['components']
        assert client.post('/api/demo/annotations', json={**label, 'capture_sha256': 'bad'}).status_code == 422
        assert client.post('/api/demo/annotations', json={**label, 'duration_seconds': 123}).status_code == 422


def test_annotation_roundtrip_and_validation(tmp_path):
    with TestClient(create_app(tmp_path / "store")) as client:
        identifier = upload(client, tmp_path)
        points = {slot: [1.0, 2.0, 3.0] for slot in
                  ("left_post_base", "left_post_top", "right_post_base", "right_post_top", "crossbar_mid")}
        ok = client.post("/api/demo/annotations", json={
            "capture_id": identifier, "type": "goal", "frame_range": [0, 2],
            "points": points, "note": "标准球门，斜对视角", "labeler": "测试员",
            "annotation_context": {
                "frame_index": 2, "device_time": 1.2, "accumulated_frames": 3,
                "window_size": 3, "voxel_size": 0.3, "range": [0, 160],
                "source": "demo_accumulated_cloud"}})
        assert ok.status_code == 201, ok.text
        assert ok.json()["labels_total"] == 1
        docs = client.get(f"/api/demo/annotations?capture_id={identifier}").json()
        assert len(docs) == 1 and docs[0]["capture_sha256"]
        label = docs[0]["labels"][0]
        assert label["type"] == "goal" and label["note"] == "标准球门，斜对视角"
        assert label["annotation_context"]["window_size"] == 3
        assert label['observation_unit'] == 'accumulated_window'
        assert label['review_status'] == 'PENDING_REVIEW'
        # 绑定校验：非法形状全部 422
        bad_cases = [
            {"type": "fence"},  # 类型非法
            {"type": "goal", "frame_range": [5, 2]},  # 区间倒挂
            {"type": "goal", "frame_range": [0, 10**9]},  # 越界
            {"type": "goal", "frame_range": [0, 2], "points": {"left_post_base": [1, 2]}},  # 缺槽位
            {"type": "goal", "frame_range": [0, 2], "points": {**points, "extra": [1, 2, 3]}},  # 多槽位
            {"type": "non_goal_structure", "frame_range": [0, 2], "points": points},  # 负样本槽位错
        ]
        for case in bad_cases:
            body = {"capture_id": identifier, "note": "", "labeler": "x", **case}
            response = client.post("/api/demo/annotations", json=body)
            assert response.status_code == 422, case
        # 框式标注（新交互）
        box_ok = client.post("/api/demo/annotations", json={
            "capture_id": identifier, "type": "goal", "frame_range": [0, 2],
            "box": {"center": [79.7, 6.3, 1.3], "size": [0.6, 7.32, 2.44], "yaw": 0.8},
            "note": "框式标注", "labeler": "测试员"})
        assert box_ok.status_code == 201, box_ok.text
        docs = client.get(f"/api/demo/annotations?capture_id={identifier}").json()
        assert docs[0]["labels"][-1]["box"]["size"][1] == 7.32
        # 框式校验：负尺寸 / 缺字段 / 朝向 NaN 之外——非法输入 422
        for bad_box in [
            {"center": [1, 2, 3], "size": [0, 7, 2], "yaw": 0},
            {"center": [1, 2, 3], "size": [0.6, 7.32, 2.44]},
            {"center": [1, 2, 3], "size": [0.6, 7.32, 2.44], "yaw": "east"},
        ]:
            response = client.post("/api/demo/annotations", json={
                "capture_id": identifier, "type": "goal", "frame_range": [0, 2],
                "box": bad_box})
            assert response.status_code == 422, bad_box
        # 追加第二条
        second = client.post("/api/demo/annotations", json={
            "capture_id": identifier, "type": "non_goal_structure", "frame_range": [0, 2],
            "points": {"center": [110.0, -49.0, 1.2]}, "note": "围栏线段"})
        assert second.status_code == 201 and second.json()["labels_total"] == 3
        # 未知采集 404
        missing = client.post("/api/demo/annotations", json={
            "capture_id": str(uuid4()), "type": "goal", "frame_range": [0, 1],
            "points": points})
        assert missing.status_code == 404


def test_annotate_cloud_endpoint(tmp_path):
    with TestClient(create_app(tmp_path / "store")) as client:
        identifier = upload(client, tmp_path, packets=305)
        response = client.post("/api/demo/annotate/cloud", json={
            "capture_id": identifier, "start": 0, "end": 1, "voxel": 0.5})
        assert response.status_code == 200
        buffer = response.content
        if buffer:
            length = struct.unpack("<I", buffer[:4])[0]
            import json as jsonlib
            meta = jsonlib.loads(buffer[4:4 + length])
            assert meta["version"] == 1 and meta["mode"] == "annotate"
            assert meta["accumulated_frames"] == 2
            assert len(buffer) - 4 - length == meta["point_count"] * 16
        # 原始全量模式：voxel=0 不过滤任何点；夹具点全部有限，点数 = 解码行数。
        raw = client.post("/api/demo/annotate/cloud", json={
            "capture_id": identifier, "start": 0, "end": 1, "voxel": 0})
        assert raw.status_code == 200
        length = struct.unpack("<I", raw.content[:4])[0]
        import json as jsonlib
        meta = jsonlib.loads(raw.content[4:4 + length])
        assert meta["filtering"] == "none" and meta["voxel"] == 0
        assert meta["device_time"] is not None and meta["device_start_time"] is not None
        assert meta["point_count"] == meta["raw_point_count"] == 2 * 20000
        # 体素模式不增点，且 meta 必须显式声明经过了体素化，不能冒充原始叠加。
        voxel_meta = jsonlib.loads(buffer[4:4 + struct.unpack("<I", buffer[:4])[0]])
        assert voxel_meta["filtering"] == "voxel"
        assert voxel_meta["point_count"] <= meta["point_count"]
        for bad in [{"start": 5, "end": 2}, {"start": 0, "end": 999},
                    {"start": 0, "end": 1, "voxel": -0.5}, {"start": 0, "end": 1, "voxel": 3.0}]:
            r = client.post("/api/demo/annotate/cloud", json={"capture_id": identifier, **bad})
            assert r.status_code == 422, bad


def test_annotate_timeline_aligns_frame_time_and_packets(tmp_path):
    with TestClient(create_app(tmp_path / "store")) as client:
        identifier = upload(client, tmp_path, packets=205)
        data = client.get(f"/api/demo/annotate/timeline?capture_id={identifier}").json()
        assert data["frame_count"] == 3 and len(data["frames"]) == 3
        assert data["fields"] == ["index", "device_start_time", "device_time",
                                  "packet_count", "point_count", "complete"]
        first, second, third = data["frames"]
        assert first[0] == 0 and second[0] == 1
        # 每帧的包数与完整性直接来自解码索引：完整帧 100 包，尾帧 5 包。
        assert first[3] == 100 and second[3] == 100 and third[3] == 5
        assert first[5] is True and third[5] is False
        for row in (first, second, third):
            assert row[1] is not None and row[2] is not None and row[1] <= row[2]
        # 设备时间单调不减：视频轨的时间轴与帧号不会互相矛盾。
        assert first[2] <= second[1] + 1e-6
        assert client.get(f"/api/demo/annotate/timeline?capture_id={uuid4()}").status_code == 404


def test_annotate_analyze_validation_and_statuses(tmp_path):
    with TestClient(create_app(tmp_path / "store")) as client:
        identifier = upload(client, tmp_path, packets=305)
        stroke = [[0.0, 0.0], [1.0, 0.0]]
        ok = client.post("/api/demo/annotate/analyze", json={
            "capture_id": identifier, "start": 0, "end": 2, "strokes": [stroke]})
        assert ok.status_code == 200
        # 夹具点全部落在同一位置，区域点数不足时必须显式返回状态而不是编造框。
        assert ok.json()["status"] in ("insufficient_points", "degenerate_span")
        assert "box" not in ok.json()
        for bad in [
            {"strokes": []},
            {"strokes": [stroke, []]},
            {"strokes": [[[0, 0]]], "brush_radius": 0.05},
            {"strokes": [[[0, 0]]], "brush_radius": 9},
            {"strokes": [[[0, 0, 0]]]},
        ]:
            r = client.post("/api/demo/annotate/analyze", json={
                "capture_id": identifier, "start": 0, "end": 2, **bad})
            assert r.status_code == 422, bad
        # JSON 传输层不允许 NaN，用原始 body 验证服务端仍拒绝非有限坐标。
        nan_body = ('{"capture_id": "%s", "start": 0, "end": 2, '
                    '"strokes": [[[NaN, 0.0]]]}' % identifier)
        r = client.post("/api/demo/annotate/analyze", content=nan_body,
                        headers={"Content-Type": "application/json"})
        assert r.status_code == 422
        assert client.post("/api/demo/annotate/analyze", json={
            "capture_id": str(uuid4()), "start": 0, "end": 1,
            "strokes": [stroke]}).status_code == 404
