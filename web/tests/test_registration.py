"""SYNTHETIC: rigid registration correctness, not NA70 acceptance evidence."""
import numpy as np

from backend.registration import configuration, register, sample


def test_synthetic_rigid_pose():
    rng = np.random.default_rng(12)
    target = rng.uniform(-3, 3, (3000, 3))
    angle = 0.025
    rotation = np.array([[np.cos(angle), -np.sin(angle), 0], [np.sin(angle), np.cos(angle), 0], [0, 0, 1]])
    shift = np.array([0.06, -0.04, 0.03])
    source = (target - shift) @ rotation
    pose, quality = register(source, target, np.eye(4), configuration())
    assert pose is not None
    np.testing.assert_allclose(pose[:3, :3], rotation, atol=1e-5)
    np.testing.assert_allclose(pose[:3, 3], shift, atol=1e-5)
    assert quality['rmse_m'] < 1e-5


def test_synthetic_reject_missing_overlap_and_plane():
    rng = np.random.default_rng(7)
    points = rng.uniform(-1, 1, (200, 3))
    assert register(points, points + 20, np.eye(4), configuration())[0] is None
    points[:, 2] = 0
    assert register(points, points, np.eye(4), configuration())[1]['reason'] == 'degenerate_geometry'


def test_invalid_points_filtered():
    points = np.array([[0, 0, 0], [np.nan, 1, 2], [np.inf, 0, 0], [1, 2, 3]])
    np.testing.assert_array_equal(sample(points, configuration(), 100), [[1, 2, 3]])


def test_synthetic_loss_stops_fusion(monkeypatch):
    from backend import registration
    rng = np.random.default_rng(2)
    cloud = rng.uniform(1, 4, (1000, 3))
    clouds = [cloud, cloud + 30, cloud]
    monkeypatch.setattr(registration, 'decode_frame', lambda path, frame: clouds[frame['index']])
    result = registration.analyze_registration(None, [dict(index=i, time=i) for i in range(3)], lambda: False, lambda _: None)
    assert [r['accepted'] for r in result['frames']] == [True, False, False]
    assert result['frames'][2]['pose'] is None
    assert result['frames'][2]['reason'] == 'tracking_lost_restart_required'


def test_registration_api_job(tmp_path):
    import time
    from uuid import uuid4
    from fastapi.testclient import TestClient
    from backend.app import create_app
    from test_decoder import make_pcap, payload
    from test_api import wait_ready

    source = make_pcap(tmp_path / 'synthetic.pcap', [payload()] * 101)
    identifier = str(uuid4())
    with TestClient(create_app(tmp_path / 'store')) as client:
        client.post('/api/captures', params=dict(id=identifier, filename='synthetic.pcap'), content=source.read_bytes())
        assert wait_ready(client, identifier)['status'] == 'ready'
        endpoint = f'/api/captures/{identifier}/registration'
        assert client.get(endpoint).json()['status'] == 'idle'
        assert client.post(endpoint).status_code == 200
        for _ in range(200):
            result = client.get(endpoint).json()
            if result['status'] != 'running':
                break
            time.sleep(0.01)
        assert result['status'] == 'ready'
        assert result['result']['imu'] is False
        assert result['result']['frame_count'] == 2
