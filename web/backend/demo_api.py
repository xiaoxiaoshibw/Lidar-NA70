"""Demo session backend: drives the goal pipeline over a captured PCAP.

A session replays one capture at its device frame rate through
``GoalDemoPipeline`` on a background thread and keeps the latest snapshot plus
the binary clouds for polling clients.  Sessions are in-memory and die with
the process (single-user local service); the capture cache itself is untouched.

Annotation support lives here too: on-demand accumulated clouds for arbitrary
frame ranges (the human labeler's canvas) and a small JSON store for manual
labels under ``web/data/annotations/``.
"""
from __future__ import annotations

import json
import math
import struct
import threading
import time
import uuid as uuid_module
from collections import Counter, deque
from copy import deepcopy
from uuid import UUID, uuid4

import numpy as np
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from .decoder import decode_frame
from .goal_detection.accumulation import Accumulator
from .goal_detection.annotate_analysis import analyze_brush_region
from .goal_detection.buffer import StampedCloud
from .goal_detection.config import (configuration_version, load_configuration,
                                    merge_configuration)
from .goal_detection.pipeline import GoalDemoPipeline
from .goal_detection.window_annotation import validate_window_label

MAX_SESSIONS = 2
ANNOTATION_DIR_NAME = "annotations"
POINT_SLOTS = ("left_post_base", "left_post_top", "right_post_base", "right_post_top", "crossbar_mid")


def create_demo_router(store):
    router = APIRouter(prefix="/api/demo")
    sessions: dict[str, dict] = {}
    guard = threading.Lock()

    class DemoSession:
        def __init__(self, capture_id: str, overrides: dict | None, speed: float):
            item = store.get(capture_id)
            if item["meta"]["status"] != "ready":
                raise HTTPException(409, "记录尚未就绪")
            self.capture_id = capture_id
            self.capture_sha256 = item['meta'].get('sha256', '')
            self.frames = item["frames"]
            self.pcap = store.directory(capture_id) / "capture.pcap"
            self.cfg = load_configuration(overrides=overrides)
            # demo 会话聚焦已确认的真值球筐邻域（webui.demo_roi，null = 全图）。
            demo_roi = self.cfg["webui"].get("demo_roi")
            requested_accumulation = (overrides or {}).get("accumulation", {})
            # Annotation sessions explicitly send roi=null to show the full
            # accumulated scene.  Replay/demo sessions keep the configured
            # goal ROI unless the caller overrides it.
            if demo_roi and "roi" not in requested_accumulation:
                self.cfg["accumulation"]["roi"] = list(demo_roi)
            self.speed = max(min(float(speed), 10.0), 0.25)
            self.pipeline = GoalDemoPipeline(self.cfg)
            self.lock = threading.Lock()
            self.stop = threading.Event()
            self.control_lock = threading.Lock()
            self.paused = False
            self.seek_to: int | None = None
            self.thread: threading.Thread | None = None
            self.snapshot: dict = {"status": "starting", "error": None, "paused": False}
            self.single_cloud: np.ndarray | None = None
            self.accumulated_cloud: tuple[np.ndarray, np.ndarray] | None = None
            self.accumulated_provenance = None
            self.debug: dict | None = None
            self.observability: dict = {}
            self.logs = deque(maxlen=1000)
            self.metric_states = Counter()
            self.metric_latencies = deque(maxlen=500)
            self.last_goal_state: str | None = None
            self.fps = 0.0
            self.processing_ms = 0.0

        def run(self) -> None:
            try:
                source = self.pcap.open("rb")
            except OSError as error:
                with self.lock:
                    self.snapshot = {"status": "error", "error": f"无法打开采集文件: {error}"}
                return
            start_wall = time.monotonic()
            first_time = None
            last_tick = time.monotonic()
            frames_done = 0
            try:
                index = 0
                while index < len(self.frames):
                    if self.stop.is_set():
                        break
                    with self.control_lock:
                        if self.seek_to is not None:
                            index = max(0, min(self.seek_to, len(self.frames) - 1))
                            self.pipeline = GoalDemoPipeline(self.cfg)
                            first_time = None
                            start_wall = time.monotonic()
                            self.seek_to = None
                            with self.lock:
                                self.single_cloud = None
                                self.accumulated_cloud = None
                        paused = self.paused
                    if paused:
                        self.stop.wait(0.05)
                        continue
                    frame = self.frames[index]
                    t0 = time.perf_counter()
                    points = decode_frame(self.pcap, frame, source=source)
                    meta_device_time = None
                    packet_times = frame.get("packet_device_times") or []
                    for value in reversed(packet_times):
                        if value is not None:
                            meta_device_time = float(value)
                            break
                    result = self.pipeline.process_frame(points, frame["index"], meta_device_time)
                    elapsed_ms = (time.perf_counter() - t0) * 1000.0
                    frames_done += 1
                    now = time.monotonic()
                    if now - last_tick >= 0.5:
                        self.fps = round(frames_done / (now - last_tick + 1e-9), 1)
                        last_tick, frames_done = now, 0
                    self.processing_ms = 0.2 * elapsed_ms + 0.8 * self.processing_ms
                    if first_time is None:
                        first_time = meta_device_time or 0.0
                    with self.lock:
                        goal = result["goal"]
                        window = self.pipeline.accumulator.buffer.window()
                        lo, hi = window[0].frame_index, window[-1].frame_index
                        first_device = self.frames[lo].get('device_start_time')
                        last_device = self.frames[hi].get('device_time')
                        window_context = {
                            'capture_sha256': self.capture_sha256,
                            'observation_unit': 'accumulated_window',
                            'frame_range': [lo, hi], 'coordinate_frame': 'lidar_link',
                            'cloud_id': f'{self.capture_sha256}:{configuration_version(self.cfg)}:{result["accumulated_cloud_id"]}',
                            'device_time_range': [first_device, last_device],
                            'duration_seconds': last_device-first_device if first_device is not None and last_device is not None else None,
                        }
                        self.snapshot = {
                            'window_context': window_context,
                            "status": "running",
                            "error": None,
                            "timestamp": time.time(),
                            "frame_index": result["frame_index"],
                            "device_time": result["device_time"],
                            "accumulated_frames": result["accumulated_frames"],
                            "accumulated_points": result["accumulated_points"],
                            "window_raw_points": result["window_raw_points"],
                            "window_size": self.pipeline.accumulator.window_size,
                            "imu": False,
                            "lidar_online": True,
                            "speed": self.speed,
                            "paused": self.paused,
                            "fps": self.fps,
                            "processing_ms": round(self.processing_ms, 1),
                            "config_version": configuration_version(self.cfg),
                            "strategy": result.get("strategy"),
                            "focus": result.get("focus"),
                            "goal": goal,
                        }
                        self.single_cloud = points
                        self.accumulated_cloud = result["accumulated_cloud"]
                        self.accumulated_provenance = self.pipeline.accumulator.provenance
                        self.observability = result.get("observability") or {}
                        self.debug = {
                            "frame_index": result["frame_index"],
                            "single_candidates": result["single_candidates"],
                            "accumulated_candidates": result["accumulated_candidates"],
                            "single_rejected_candidates": result.get("single_rejected_candidates", []),
                            "accumulated_rejected_candidates": result.get("accumulated_rejected_candidates", []),
                            "focus": result.get("focus"),
                            "bev_regions": result.get("bev_regions"),
                            "z_refine": result.get("z_refine"),
                            "association": result.get("association"),
                            "selected_measurement": result.get("selected_measurement"),
                            "selected_measurement_source": result.get("selected_measurement_source"),
                            "accumulated_cloud_id": result.get("accumulated_cloud_id"),
                            "execution_trace": result.get("execution_trace"),
                            "pipelines": {
                                name: {"frame_index": entry["frame_index"],
                                       "source": entry["source"],
                                       "stages": entry["stages"]}
                                for name, entry in self.observability.items()
                            },
                        }
                        state_name = str(goal.get("state", "SEARCHING"))
                        self.metric_states[state_name] += 1
                        self.metric_latencies.append(round(elapsed_ms, 3))
                        self.logs.append({
                            "timestamp": time.time(), "frame_id": result["frame_index"],
                            "module": "pipeline", "level": "INFO",
                            "message": f"{state_name}: {len(result['accumulated_candidates'])} candidates",
                            "data": {"processing_ms": round(elapsed_ms, 3),
                                     "association": result.get("association")},
                        })
                        if self.last_goal_state != state_name:
                            self.logs.append({
                                "timestamp": time.time(), "frame_id": result["frame_index"],
                                "module": "tracker", "level": "INFO",
                                "message": f"state {self.last_goal_state or 'INIT'} -> {state_name}",
                                "data": {"goal_id": goal.get("id")},
                            })
                            self.last_goal_state = state_name
                    index += 1
                    # Pace to device time so replay looks live; speed divides the wait.
                    if meta_device_time is not None:
                        target = start_wall + (meta_device_time - first_time) / self.speed
                        wait = target - time.monotonic()
                        if wait > 0:
                            self.stop.wait(wait)
                with self.lock:
                    if self.snapshot.get("status") != "error":
                        self.snapshot["status"] = "done"
            except Exception as error:  # keep the session queryable on failure
                with self.lock:
                    self.snapshot = {"status": "error", "error": repr(error)}

    @router.get("/captures")
    def demo_captures():
        """只返回适合球门跟踪演示的采集（按 YAML 的文件名过滤，避免误选无关数据）。"""
        cfg = load_configuration()
        filters = tuple(cfg["webui"]["capture_name_filter"])
        with store.lock:
            result = []
            for key, item in store.items.items():
                meta = item["meta"]
                if meta["status"] != "ready":
                    continue
                if filters and not any(f in meta.get("filename", "") for f in filters):
                    continue
                result.append({"id": key, "filename": meta.get("filename", ""),
                               "frame_count": meta.get("frame_count", 0),
                               "duration": meta.get("duration", 0)})
            return sorted(result, key=lambda entry: entry["filename"])

    @router.post("/sessions", status_code=202)
    def create_session(payload: dict):
        capture_id = payload.get("capture_id")
        try:
            capture_id = str(UUID(str(capture_id)))
        except ValueError:
            raise HTTPException(422, "capture_id 必须是 UUID")
        overrides = payload.get("config") or None
        if overrides and not isinstance(overrides, dict):
            raise HTTPException(422, "config 必须是对象")
        speed = payload.get("speed", 1.0)
        if not isinstance(speed, (int, float)) or isinstance(speed, bool) or not 0.25 <= speed <= 10:
            raise HTTPException(422, "speed 必须在 0.25–10 之间")
        with guard:
            # 清理已结束的会话，避免列表无限增长。
            for key in [k for k, s in sessions.items()
                        if s.snapshot.get("status") in ("done", "error") or s.stop.is_set()]:
                sessions.pop(key, None)
            live = [s for s in sessions.values() if not s.stop.is_set()
                    and s.snapshot.get("status") in ("starting", "running")]
            if len(live) >= MAX_SESSIONS:
                raise HTTPException(409, "已有演示会话在运行，请先停止")
            try:
                session = DemoSession(capture_id, overrides, speed)
            except HTTPException:
                raise
            except ValueError as error:
                raise HTTPException(422, str(error))
            session_id = uuid4().hex
            sessions[session_id] = session
        session.thread = threading.Thread(target=session.run, daemon=True,
                                          name=f"goal-demo-{session_id[:8]}")
        session.thread.start()
        return {"session_id": session_id, "status": "starting"}

    @router.get("/sessions")
    def list_sessions():
        with guard:
            return [{"session_id": sid, "capture_id": s.capture_id,
                     "status": s.snapshot.get("status")} for sid, s in sessions.items()]

    @router.get("/sessions/{session_id}")
    def session_state(session_id: str):
        session = _session(session_id)
        with session.lock:
            # Snapshot dicts are replaced whole each frame, never mutated in place.
            return dict(session.snapshot)

    @router.get("/sessions/{session_id}/cloud")
    def session_cloud(session_id: str, mode: str = Query("accumulated")):
        session = _session(session_id)
        with session.lock:
            snapshot = dict(session.snapshot)
            if mode == "single":
                points = None if session.single_cloud is None else session.single_cloud[:, :4]
            elif mode == "accumulated":
                acc = session.accumulated_cloud
                if acc is not None:
                    points = np.column_stack((acc[0], acc[1]))
                else:
                    points = None
            else:
                raise HTTPException(422, "mode 必须是 single 或 accumulated")
        if points is None or len(points) == 0:
            return Response(b"", media_type="application/octet-stream",
                            headers={"X-Frame-Index": "-1"})
        header = json.dumps({
            "version": 1, "fields": ["x", "y", "z", "intensity"], "dtype": "float32-le",
            "index": snapshot.get("frame_index", -1),
            "device_time": snapshot.get("device_time"),
            "window_context": snapshot.get("window_context") if mode == 'accumulated' else None,
            "point_count": int(len(points)),
            "mode": mode, "accumulated_frames": snapshot.get("accumulated_frames"),
        }, ensure_ascii=True).encode()
        header += b" " * (-len(header) % 4)
        body = struct.pack("<I", len(header)) + header + points.astype("<f4", copy=False).tobytes()
        return Response(body, media_type="application/octet-stream",
                        headers={"X-Frame-Index": str(snapshot.get("frame_index", -1)),
                                 "Cache-Control": "no-store"})

    @router.get("/sessions/{session_id}/debug")
    def session_debug(session_id: str):
        session = _session(session_id)
        with session.lock:
            return session.debug or {"frame_index": -1}

    @router.get("/sessions/{session_id}/pipeline")
    def session_pipeline(session_id: str, source: str = Query("accumulated")):
        session = _session(session_id)
        if source not in ("single", "accumulated"):
            raise HTTPException(422, "source 必须是 single 或 accumulated")
        with session.lock:
            entry = session.observability.get(source)
            if entry is None:
                return {"frame_index": -1, "source": source, "stages": []}
            return {"frame_index": entry["frame_index"], "source": source,
                    "stages": entry["stages"]}

    @router.get("/sessions/{session_id}/provenance")
    def session_provenance(session_id: str, voxel_index: int = Query(..., ge=0),
                           limit: int = Query(4096, ge=1, le=20000)):
        """Resolve one accumulated-cloud row to original (frame, point) IDs."""
        session = _session(session_id)
        with session.lock:
            provenance = session.accumulated_provenance
            cloud_id = None if session.debug is None else session.debug.get("accumulated_cloud_id")
            frame_index = session.snapshot.get("frame_index", -1)
        if provenance is None or voxel_index >= provenance.output_count:
            raise HTTPException(404, "voxel_index 不存在于当前累积云")
        refs = provenance.sources_for(voxel_index)
        return {
            "frame_index": frame_index, "cloud_id": cloud_id,
            "voxel_index": voxel_index, "total": int(len(refs)),
            "returned": min(int(len(refs)), limit),
            "truncated": len(refs) > limit,
            "source_points": refs[:limit].tolist(),
        }

    @router.get("/sessions/{session_id}/debug/cloud")
    def session_debug_cloud(session_id: str, stage: str = Query("fov"),
                            source: str = Query("accumulated"),
                            candidate_id: str | None = None):
        session = _session(session_id)
        if source not in ("single", "accumulated"):
            raise HTTPException(422, "source 必须是 single 或 accumulated")
        with session.lock:
            entry = session.observability.get(source)
            class_stage = {
                "finite": "raw", "range": "fov", "pole_extract": "cluster",
                "pair_score": "goal_structure", "nms": "candidate_score",
                "bev_proposal": "xy_roi", "z_refine": "bounding_box",
                "focus_crop": "fov", "region_best_selection": "candidate_score",
            }.get(stage, stage)
            classes = None if entry is None else entry.get("classes", {}).get(class_stage)
            if classes is not None:
                classes = classes.copy()
            highlights = None if classes is None else np.zeros(len(classes), dtype=np.uint8)
            if candidate_id and classes is not None and session.debug is not None:
                key = "single_candidates" if source == "single" else "accumulated_candidates"
                rejected_key = ("single_rejected_candidates" if source == "single"
                                else "accumulated_rejected_candidates")
                selected = next((candidate for candidate in [
                                    *session.debug.get(key, []),
                                    *session.debug.get(rejected_key, [])]
                                 if candidate.get("candidate_id") == candidate_id), None)
                if selected is not None:
                    indexes = np.asarray(selected.get("supporting_point_indices", []), dtype=np.int64)
                    indexes = indexes[(indexes >= 0) & (indexes < len(classes))]
                    highlights[indexes] = 1
            if source == "single":
                points = session.single_cloud
            else:
                acc = session.accumulated_cloud
                points = None if acc is None else np.column_stack((acc[0], acc[1]))
                # Accumulated analysis must not make the current complete scan
                # disappear.  RAW shows that scan directly; later stages draw
                # the accumulated active/support points over a dim rejected
                # copy of the current raw scan.
                raw = session.single_cloud
                if raw is not None and len(raw):
                    if stage == "raw":
                        points = raw[:, :4]
                        classes = np.ones(len(points), dtype=np.uint8)
                        highlights = np.zeros(len(points), dtype=np.uint8)
                    elif points is not None:
                        points = np.vstack((raw[:, :4], points[:, :4]))
                        classes = np.concatenate((np.full(len(raw), 3, dtype=np.uint8), classes))
                        highlights = np.concatenate((np.zeros(len(raw), dtype=np.uint8),
                                                     highlights))
            frame_index = session.snapshot.get("frame_index", -1)
        if classes is None:
            raise HTTPException(422, f"未知 stage: {stage}")
        if points is None or len(points) == 0:
            return Response(b"", media_type="application/octet-stream",
                            headers={"X-Frame-Index": "-1"})
        count = min(len(points), len(classes), len(highlights))
        payload = np.column_stack((points[:count, :4],
                                   classes[:count].astype(np.float32),
                                   highlights[:count].astype(np.float32)))
        header = json.dumps({
            "version": 3,
            "fields": ["x", "y", "z", "intensity", "classification", "highlight"],
            "dtype": "float32-le", "index": frame_index,
            "point_count": int(count), "source": source, "stage": stage,
            "classes": {"rejected": 0, "active": 1, "supporting": 2,
                        "context_raw": 3},
            "highlight": {"none": 0, "selected_candidate_support": 1},
            "complete_raw_visible": True,
        }, ensure_ascii=True).encode()
        header += b" " * (-len(header) % 4)
        body = struct.pack("<I", len(header)) + header + payload.astype("<f4", copy=False).tobytes()
        return Response(body, media_type="application/octet-stream",
                        headers={"X-Frame-Index": str(frame_index), "Cache-Control": "no-store"})

    @router.get("/sessions/{session_id}/logs")
    def session_logs(session_id: str, module: str | None = None,
                     level: str | None = None, limit: int = Query(200, ge=1, le=1000)):
        session = _session(session_id)
        with session.lock:
            rows = list(session.logs)
        if module:
            rows = [row for row in rows if row["module"] == module]
        if level:
            rows = [row for row in rows if row["level"] == level.upper()]
        return {"items": rows[-limit:], "total": len(rows)}

    @router.get("/sessions/{session_id}/metrics")
    def session_metrics(session_id: str):
        session = _session(session_id)
        with session.lock:
            latencies = np.asarray(session.metric_latencies, dtype=float)
            return {
                "frame_index": session.snapshot.get("frame_index", -1),
                "frames_observed": int(sum(session.metric_states.values())),
                "state_frames": dict(session.metric_states),
                "processing_ms": {
                    "mean": round(float(latencies.mean()), 3) if len(latencies) else None,
                    "p50": round(float(np.percentile(latencies, 50)), 3) if len(latencies) else None,
                    "p95": round(float(np.percentile(latencies, 95)), 3) if len(latencies) else None,
                },
                "config_version": configuration_version(session.cfg),
            }

    @router.get("/sessions/{session_id}/config")
    def session_config(session_id: str):
        session = _session(session_id)
        with session.lock:
            return {"version": configuration_version(session.cfg),
                    "config": deepcopy(session.cfg)}

    @router.put("/sessions/{session_id}/config")
    def update_session_config(session_id: str, payload: dict):
        session = _session(session_id)
        try:
            cfg = merge_configuration(session.cfg, payload)
        except ValueError as error:
            raise HTTPException(422, str(error))
        with session.control_lock:
            session.cfg = cfg
            session.pipeline = GoalDemoPipeline(cfg)
            session.seek_to = int(session.snapshot.get("frame_index", 0))
        with session.lock:
            session.logs.append({
                "timestamp": time.time(), "frame_id": session.snapshot.get("frame_index", -1),
                "module": "config", "level": "INFO", "message": "configuration updated",
                "data": {"version": configuration_version(cfg), "overrides": payload},
            })
        return {"status": "ok", "version": configuration_version(cfg),
                "config": deepcopy(cfg)}

    @router.get("/sessions/{session_id}/snapshot")
    def session_debug_snapshot(session_id: str):
        session = _session(session_id)
        with session.lock:
            return {
                "schema_version": 1,
                "snapshot": dict(session.snapshot),
                "pipeline": None if session.debug is None else session.debug.get("pipelines"),
                "candidates": None if session.debug is None else {
                    "single": session.debug.get("single_candidates", []),
                    "accumulated": session.debug.get("accumulated_candidates", []),
                    "single_rejected": session.debug.get("single_rejected_candidates", []),
                    "accumulated_rejected": session.debug.get("accumulated_rejected_candidates", []),
                },
                "association": None if session.debug is None else session.debug.get("association"),
                "selected_measurement": (None if session.debug is None else
                                         session.debug.get("selected_measurement")),
                "metrics": {"state_frames": dict(session.metric_states),
                            "frames_observed": int(sum(session.metric_states.values()))},
                "config_version": configuration_version(session.cfg),
                "logs": list(session.logs)[-100:],
            }

    @router.post("/sessions/{session_id}/control")
    def session_control(session_id: str, payload: dict):
        """Pause/resume/seek the replay.  Seeking resets the pipeline and the
        accumulation window, then continues from the target frame."""
        session = _session(session_id)
        action = payload.get("action")
        with session.control_lock:
            if action == "pause":
                session.paused = True
            elif action == "resume":
                session.paused = False
            elif action == "seek":
                frame = payload.get("frame")
                if not isinstance(frame, int) or isinstance(frame, bool) \
                        or not 0 <= frame < len(session.frames):
                    raise HTTPException(422, f"frame 必须在 0–{len(session.frames) - 1} 之间")
                session.seek_to = frame
                session.paused = False
            else:
                raise HTTPException(422, "action 必须是 pause / resume / seek")
        with session.lock:
            session.snapshot["paused"] = session.paused
        return {"status": "ok", "paused": session.paused,
                "frame_index": session.snapshot.get("frame_index")}

    @router.delete("/sessions/{session_id}", status_code=204)
    def stop_session(session_id: str):
        with guard:
            session = sessions.pop(session_id, None)
        if session is None:
            raise HTTPException(404, "会话不存在")
        session.stop.set()
        return Response(status_code=204)

    def _session(session_id: str):
        session = sessions.get(session_id)
        if session is None:
            raise HTTPException(404, "会话不存在")
        return session

    # ------------------------------------------------------- annotation support

    annotate_lock = threading.Lock()
    annotate_cache: dict[tuple, np.ndarray] = {}

    def _annotate_window(capture_id: str, start: int, end: int) -> tuple[list, Path, str]:
        with store.lock:
            item = store.get(capture_id)
            if item["meta"]["status"] != "ready":
                raise HTTPException(409, "记录尚未就绪")
            frames = item["frames"]
            if not isinstance(start, int) or not isinstance(end, int) \
                    or not 0 <= start <= end < len(frames):
                raise HTTPException(422, f"start/end 非法（0 ≤ start ≤ end < {len(frames)}）")
            if end - start + 1 > 400:
                raise HTTPException(422, "一次最多叠加 400 帧")
            return [dict(frame) for frame in frames[start:end + 1]], \
                store.directory(capture_id) / "capture.pcap", \
                item["meta"].get("sha256", "")

    def _build_annotate_cloud(capture_id: str, frames: list, pcap: Path, sha: str,
                              voxel: float, range_min: float,
                              range_max: float) -> tuple[np.ndarray, dict]:
        """Accumulate the frame window.  ``voxel == 0`` is the annotate
        workbench's raw mode: every decoded point is kept, with no range/ROI
        gate and no voxel downsample — the labeler must see the full point
        set.  Only rows with non-finite coordinates are dropped (they have no
        displayable position); the dropped count is reported, never hidden."""
        first_device = frames[0].get("device_start_time")
        last_device = frames[-1].get("device_time")
        extra = {"voxel": round(float(voxel), 4),
                 "filtering": "none" if voxel == 0 else "voxel",
                 "device_start_time": first_device,
                 "device_time": last_device,
                 "window_context": {
                     "capture_sha256": sha,
                     "observation_unit": "accumulated_window",
                     "frame_range": [frames[0]["index"], frames[-1]["index"]],
                     "coordinate_frame": "lidar_link",
                     "cloud_id": f"annotate-raw:{sha[:16]}:{frames[0]['index']}-{frames[-1]['index']}",
                     "device_time_range": [first_device, last_device],
                     "duration_seconds": (last_device - first_device
                                          if first_device is not None and last_device is not None
                                          else None),
                 }}
        if voxel == 0:
            clouds = []
            raw_total = 0
            with pcap.open("rb") as source:
                for frame in frames:
                    points = decode_frame(pcap, frame, source=source)
                    raw_total += len(points)
                    finite = np.isfinite(points).all(axis=1)
                    if finite.all():
                        clouds.append(points)
                    elif finite.any():
                        clouds.append(points[finite])
            cloud = np.vstack(clouds) if clouds else np.empty((0, 4), np.float32)
            extra["raw_point_count"] = raw_total
            return cloud, extra
        cfg = load_configuration(overrides={"accumulation": {
            "window_size": len(frames), "voxel_size": float(voxel),
            "range": {"min": float(range_min), "max": float(range_max)},
            "max_points": 2000000}})
        acc = Accumulator(cfg["accumulation"])
        with pcap.open("rb") as source:
            for frame in frames:
                points = decode_frame(pcap, frame, source=source)
                acc.push(StampedCloud(points, frame_index=frame["index"]))
        xyz, intensity = acc.accumulated()
        return np.column_stack((xyz, intensity)), extra

    def _cached_annotate_cloud(capture_id: str, start: int, end: int, voxel: float,
                               range_min: float, range_max: float) -> tuple[np.ndarray, dict]:
        key = (capture_id, start, end, float(voxel), float(range_min), float(range_max))
        cached = annotate_cache.get(key)
        if cached is not None:
            return cached
        with annotate_lock:
            cached = annotate_cache.get(key)
            if cached is not None:
                return cached
            frames, pcap, sha = _annotate_window(capture_id, start, end)
            cloud, extra = _build_annotate_cloud(capture_id, frames, pcap, sha,
                                                 voxel, range_min, range_max)
            annotate_cache.clear()
            annotate_cache[key] = (cloud, extra)
            return cloud, extra

    @router.post("/annotate/cloud")
    def annotate_cloud(payload: dict):
        """Accumulate an arbitrary frame range for manual labeling (v1 binary).

        ``voxel: 0`` selects the raw annotate mode: the full decoded point set
        of the window with no range gate and no downsampling — what the
        labeler sees is exactly what the annotation refers to."""
        capture_id = payload.get("capture_id")
        try:
            capture_id = str(UUID(str(capture_id)))
        except ValueError:
            raise HTTPException(422, "capture_id 必须是 UUID")
        start, end = payload.get("start", 0), payload.get("end", 0)
        frames, pcap, _sha = _annotate_window(capture_id, start, end)
        voxel = payload.get("voxel", 0)
        if not isinstance(voxel, (int, float)) or isinstance(voxel, bool) \
                or not (voxel == 0 or 0.05 <= voxel <= 2.0):
            raise HTTPException(422, "voxel 必须为 0（原始全量）或 0.05–2.0")
        range_min = payload.get("range_min", 0.0)
        range_max = payload.get("range_max", 1200.0)
        if voxel > 0:
            if not isinstance(range_min, (int, float)) or not isinstance(range_max, (int, float)) \
                    or isinstance(range_min, bool) or not 0 <= range_min < range_max <= 2000:
                raise HTTPException(422, "range 非法")
        cloud, extra = _cached_annotate_cloud(capture_id, start, end, float(voxel),
                                              float(range_min), float(range_max))
        return _cloud_response(cloud, {"index": end, "mode": "annotate",
                                       "start": start, "end": end,
                                       "accumulated_frames": end - start + 1, **extra})

    @router.get("/annotate/timeline")
    def annotate_timeline(capture_id: str):
        """Per-frame alignment data for the annotation video track.

        Every row comes from the same immutable frame index the cloud decoder
        uses, so frame number, device timestamps and packet counts cannot
        drift apart.  Rows are compact arrays:
        ``[index, device_start_time, device_time, packet_count, point_count, complete]``.
        """
        try:
            capture_id = str(UUID(str(capture_id)))
        except ValueError:
            raise HTTPException(422, "capture_id 必须是 UUID")
        with store.lock:
            item = store.get(capture_id)
            if item["meta"]["status"] != "ready":
                raise HTTPException(409, "记录尚未就绪")
            frames = item["frames"]
            rows = [[frame["index"], frame.get("device_start_time"), frame.get("device_time"),
                     frame.get("packet_count"), frame.get("point_count"),
                     bool(frame.get("complete"))] for frame in frames]
            return {"capture_id": capture_id, "frame_count": len(frames),
                    "duration": item["meta"].get("duration", 0),
                    "fields": ["index", "device_start_time", "device_time",
                               "packet_count", "point_count", "complete"],
                    "frames": rows}

    @router.post("/annotate/analyze")
    def annotate_analyze(payload: dict):
        """Measure a goal-frame proposal inside the labeler's painted region.

        Pure geometry over the raw accumulated window: posts = densest axial
        columns at both ends of the painted span, height/thickness measured
        from the same support.  The result is a proposal with explicit checks
        and warnings — the labeler decides, nothing auto-saves."""
        capture_id = payload.get("capture_id")
        try:
            capture_id = str(UUID(str(capture_id)))
        except ValueError:
            raise HTTPException(422, "capture_id 必须是 UUID")
        start, end = payload.get("start", 0), payload.get("end", 0)
        strokes = payload.get("strokes")
        if not isinstance(strokes, list) or not 1 <= len(strokes) <= 32:
            raise HTTPException(422, "strokes 必须是 1–32 条画笔折线")
        for stroke in strokes:
            if not isinstance(stroke, list) or not 1 <= len(stroke) <= 4096:
                raise HTTPException(422, "每条画笔折线需要 1–4096 个点")
            for point in stroke:
                if not isinstance(point, list) or len(point) != 2 \
                        or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                   and math.isfinite(float(v)) for v in point):
                    raise HTTPException(422, "画笔折线点必须为有限的 [x, y]")
        cfg = load_configuration()
        analysis_cfg = cfg["annotate_analysis"]
        brush = payload.get("brush_radius", analysis_cfg["brush_radius"])
        if not isinstance(brush, (int, float)) or isinstance(brush, bool) \
                or not 0.1 <= brush <= 5.0:
            raise HTTPException(422, "brush_radius 必须在 0.1–5.0 之间")
        cloud, _ = _cached_annotate_cloud(capture_id, start, end, 0.0, 0.0, 1200.0)
        result = analyze_brush_region(np.asarray(cloud[:, :3], dtype=np.float64),
                                      strokes, float(brush), analysis_cfg,
                                      cfg["goal_detection"])
        return {"capture_id": capture_id, "start": start, "end": end, **result}


    def _cloud_response(points: np.ndarray, meta: dict) -> Response:
        if points is None or len(points) == 0:
            return Response(b"", media_type="application/octet-stream",
                            headers={"X-Frame-Index": "-1"})
        header = json.dumps({
            "version": 1, "fields": ["x", "y", "z", "intensity"], "dtype": "float32-le",
            "point_count": int(len(points)), **meta,
        }, ensure_ascii=True).encode()
        header += b" " * (-len(header) % 4)
        body = struct.pack("<I", len(header)) + header + points.astype("<f4", copy=False).tobytes()
        return Response(body, media_type="application/octet-stream",
                        headers={"X-Frame-Index": str(meta.get("index", -1)),
                                 "Cache-Control": "no-store"})

    def _annotation_path(capture_id: str):
        return store.root / ANNOTATION_DIR_NAME / f"{capture_id}.json"

    @router.get("/annotations")
    def list_annotations(capture_id: str | None = None):
        root = store.root / ANNOTATION_DIR_NAME
        if not root.is_dir():
            return []
        result = []
        for path in sorted(root.glob("*.json")):
            if capture_id and path.stem != capture_id:
                continue
            try:
                document = json.loads(path.read_text("utf-8"))
            except (OSError, ValueError):
                continue
            result.append(document)
        return result

    @router.post("/annotations", status_code=201)
    def save_annotation(payload: dict):
        """Append one human label.  The labeler owns the semantics; the API only
        validates shape and binds the label to the capture's immutable hash."""
        capture_id = payload.get("capture_id")
        try:
            capture_id = str(UUID(str(capture_id)))
        except ValueError:
            raise HTTPException(422, "capture_id 必须是 UUID")
        with store.lock:
            item = store.get(capture_id)
            sha = item["meta"].get("sha256", "")
            frame_total = len(item["frames"])
        label_type = payload.get("type")
        if label_type not in ("goal", "non_goal_structure"):
            raise HTTPException(422, "type 必须是 goal 或 non_goal_structure")
        frame_range = payload.get("frame_range")
        if not isinstance(frame_range, list) or len(frame_range) != 2 \
                or not all(isinstance(v, int) and 0 <= v < frame_total for v in frame_range) \
                or frame_range[0] > frame_range[1]:
            raise HTTPException(422, f"frame_range 必须为 [start, end] 且 0 ≤ start ≤ end < {frame_total}")
        annotation_context = payload.get("annotation_context")
        if payload.get("observation_unit") is not None:
            try:
                validate_window_label(payload, sha, item["frames"])
            except (ValueError, TypeError, KeyError) as exc:
                raise HTTPException(422, str(exc))
        if annotation_context is not None:
            if not isinstance(annotation_context, dict) \
                    or set(annotation_context) != {
                        "frame_index", "device_time", "accumulated_frames",
                        "window_size", "voxel_size", "range", "source"}:
                raise HTTPException(422, "annotation_context 字段不完整")
            if not isinstance(annotation_context["frame_index"], int) \
                    or not frame_range[0] <= annotation_context["frame_index"] < frame_total:
                raise HTTPException(422, "annotation_context.frame_index 非法")
            if annotation_context["device_time"] is not None and not isinstance(
                    annotation_context["device_time"], (int, float)):
                raise HTTPException(422, "annotation_context.device_time 非法")
            if annotation_context["accumulated_frames"] is not None and (
                    not isinstance(annotation_context["accumulated_frames"], int)
                    or annotation_context["accumulated_frames"] < 1):
                raise HTTPException(422, "annotation_context.accumulated_frames 非法")
            if not isinstance(annotation_context["window_size"], int) \
                    or annotation_context["window_size"] < 1:
                raise HTTPException(422, "annotation_context.window_size 非法")
            if not isinstance(annotation_context["voxel_size"], (int, float)) \
                    or isinstance(annotation_context["voxel_size"], bool) \
                    or annotation_context["voxel_size"] < 0:
                raise HTTPException(422, "annotation_context.voxel_size 非法（0 = 原始未体素化叠加）")
            context_range = annotation_context["range"]
            if not isinstance(context_range, list) or len(context_range) != 2 \
                    or not all(isinstance(v, (int, float)) for v in context_range) \
                    or not 0 <= context_range[0] < context_range[1]:
                raise HTTPException(422, "annotation_context.range 非法")
            if annotation_context["source"] != "demo_accumulated_cloud":
                raise HTTPException(422, "annotation_context.source 非法")
        points = payload.get("points")
        box = payload.get("box")
        strokes = payload.get("strokes")
        if label_type == "goal" and points is None and box is None and not strokes:
            raise HTTPException(422, "goal 标注必须提供 box、points 或 strokes（画笔区域）")
        if label_type == "non_goal_structure" and points is None:
            raise HTTPException(422, "non_goal_structure 标注必须提供 points.center")
        if strokes is not None:
            if not isinstance(strokes, list) or not 1 <= len(strokes) <= 64:
                raise HTTPException(422, "strokes 必须是 1–64 组画笔")
            for stroke in strokes:
                if not isinstance(stroke, dict) or set(stroke) - {"points", "brush_radius"} \
                        or not isinstance(stroke.get("points"), list) \
                        or not 1 <= len(stroke["points"]) <= 4096:
                    raise HTTPException(422, "每组画笔需要 points（1–4096 个点）与可选 brush_radius")
                radius = stroke.get("brush_radius", 1.0)
                if not isinstance(radius, (int, float)) or isinstance(radius, bool) \
                        or not 0.1 <= radius <= 5.0:
                    raise HTTPException(422, "画笔 brush_radius 必须在 0.1–5.0 之间")
                for point in stroke["points"]:
                    if not isinstance(point, list) or len(point) != 2 \
                            or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                       and math.isfinite(float(v)) for v in point):
                        raise HTTPException(422, "画笔点必须为有限的 [x, y]")
        if points is not None:
            slots = POINT_SLOTS if label_type == "goal" else ("center",)
            if not isinstance(points, dict) or set(points) != set(slots):
                raise HTTPException(422, f"points 必须包含且仅包含 {slots}")
            for slot, value in points.items():
                if not isinstance(value, list) or len(value) != 3 \
                        or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                   and abs(float(v)) != float("inf") and float(v) == float(v)
                                   for v in value):
                    raise HTTPException(422, f"points.{slot} 必须为 [x, y, z] 有限数值")
        if box is not None:
            if not isinstance(box, dict) or set(box) != {"center", "size", "yaw"}:
                raise HTTPException(422, "box 必须包含 center / size / yaw")
            for key in ("center", "size"):
                value = box[key]
                if not isinstance(value, list) or len(value) != 3 \
                        or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                   and abs(float(v)) != float("inf") and float(v) == float(v)
                                   for v in value):
                    raise HTTPException(422, f"box.{key} 必须为 [x, y, z] 有限数值")
            if any(v <= 0 for v in box["size"]):
                raise HTTPException(422, "box.size 必须为正")
            if not isinstance(box["yaw"], (int, float)) or isinstance(box["yaw"], bool) \
                    or not abs(float(box["yaw"])) != float("inf"):
                raise HTTPException(422, "box.yaw 必须为有限数值")
        note = payload.get("note", "")
        if not isinstance(note, str) or len(note) > 500:
            raise HTTPException(422, "note 必须是不超过 500 字的字符串")
        labeler = payload.get("labeler", "")
        if not isinstance(labeler, str) or len(labeler) > 100:
            raise HTTPException(422, "labeler 必须是不超过 100 字的字符串")
        if annotation_context is not None and 'observation_unit' not in payload:
            # Legacy UI supplies accumulated context but no component references.
            # Preserve the label as a pending window, never as per-frame truth.
            payload = {**payload, 'observation_unit': 'accumulated_window',
                       'review_status': 'PENDING_REVIEW', 'window_contract': 'legacy_incomplete'}
        payload = {**payload, 'label_id': uuid_module.uuid4().hex[:12]}
        document = {
            "version": 2 if payload.get("observation_unit") else 1,
            "method": "MANUAL",
            "labeler": labeler,
            "saved_at": time.time(),
            "capture_id": capture_id,
            "capture_sha256": sha,
            "config_version": configuration_version(load_configuration()),
            "labels": [payload],
        }
        path = _annotation_path(capture_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with annotate_lock:
            if path.exists():
                try:
                    existing = json.loads(path.read_text("utf-8"))
                    document["labels"] = existing.get("labels", []) + [payload]
                except ValueError:
                    pass
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(document, ensure_ascii=False, indent=2), "utf-8")
            temp.replace(path)
        return {"saved": True, "labels_total": len(document["labels"]),
                "label_id": payload["label_id"], "path": str(path)}

    @router.delete("/annotations")
    def delete_annotation(capture_id: str, label_id: str):
        """Remove one human label by id.  This is the labeler's own workbench
        store; reviewed/evidence copies elsewhere are never touched here."""
        try:
            capture_id = str(UUID(str(capture_id)))
        except ValueError:
            raise HTTPException(422, "capture_id 必须是 UUID")
        if not isinstance(label_id, str) or not label_id:
            raise HTTPException(422, "label_id 必须为非空字符串")
        path = _annotation_path(capture_id)
        if not path.exists():
            raise HTTPException(404, "该采集没有标注文件")
        with annotate_lock:
            try:
                document = json.loads(path.read_text("utf-8"))
            except ValueError as error:
                raise HTTPException(500, f"标注文件损坏: {error}")
            labels = document.get("labels", [])
            kept = [label for label in labels if label.get("label_id") != label_id]
            if len(kept) == len(labels):
                raise HTTPException(404, f"label_id {label_id} 不存在")
            document["labels"] = kept
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(document, ensure_ascii=False, indent=2), "utf-8")
            temp.replace(path)
        return {"deleted": True, "labels_total": len(kept)}

    return router
